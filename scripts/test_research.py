"""Tests for Deep Research: the plan -> gather -> read -> synthesize pipeline.

Fake provider (scripted plan + report), monkeypatched web search/fetch, temp
DB - no network, no real model. Verifies query planning, URL dedup, source
capping, SSRF/error resilience, citation scaffolding, persistence as a
conversation, and the REST route.

Run: .venv\\Scripts\\python.exe scripts\\test_research.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-research-"))
from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server import research as research_mod  # noqa: E402
from server.providers.base import ModelProvider  # noqa: E402
from server.research import _parse_queries, stream_research  # noqa: E402
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolSpec  # noqa: E402


class ScriptedProvider(ModelProvider):
    """First chat() call returns the plan, the rest stream the report."""
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, plan_text: str, report_text: str):
        super().__init__({})
        self.plan_text = plan_text
        self.report_text = report_text
        self.calls = 0

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model")]

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        params: Optional[GenParams] = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        text = self.plan_text if self.calls == 1 else self.report_text
        for word in text.split(" "):
            yield StreamEvent(type="text_delta", text=word + " ")
        yield StreamEvent(type="done")


FAKE_PAGES = {
    "https://a.example/greenhouse": "Greenhouse gases trap heat in the atmosphere.",
    "https://b.example/oceans": "Oceans absorb most of the excess heat from warming.",
    "https://c.example/policy": "Carbon pricing is a widely discussed policy lever.",
}


def install_fakes(pages=FAKE_PAGES, fail=()):
    async def fake_search(query, limit=6):
        # two queries return overlapping URLs to exercise dedup
        return [{"title": u.rsplit("/", 1)[-1], "url": u, "snippet": "..."} for u in pages]

    async def fake_fetch(url, limit=4000):
        if url in fail:
            raise RuntimeError("boom")
        return pages[url]

    from server.tools import web
    web.search_web = fake_search
    web.fetch_readable = fake_fetch


async def run_research(provider, question="Why is the planet warming?"):
    conv = await db.create_conversation("inst", "fake-model", False)
    events = [ev async for ev in stream_research(conv, provider, question)]
    return conv, events


def test_ddg_unwrap():
    from server.tools.web import _unwrap_ddg
    # the exact protocol-relative redirect DuckDuckGo HTML actually returns
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FX&rut=abc"
    assert _unwrap_ddg(wrapped) == "https://en.wikipedia.org/wiki/X", _unwrap_ddg(wrapped)
    assert _unwrap_ddg("https://plain.example/p") == "https://plain.example/p"
    assert _unwrap_ddg("") == ""
    print("ddg unwrap: protocol-relative redirect -> real target URL OK")


def test_parse_queries():
    plan = '1. climate change causes\n- "ocean heat content"\nQuery: carbon policy\n\n'
    qs = _parse_queries(plan, "raw question")
    assert "climate change causes" in qs
    assert "ocean heat content" in qs
    assert "carbon policy" in qs, qs
    assert "raw question" in qs  # always appended as a fallback
    assert len(qs) <= research_mod.MAX_QUERIES
    # a blank plan still yields the raw question
    assert _parse_queries("", "just this") == ["just this"]

    # list markers are stripped but leading digits in the QUERY are preserved
    q = _parse_queries("1. 5G latency benchmarks\n2) 6G research\n2024 election results",
                       "raw")
    assert "5G latency benchmarks" in q, f"digit eaten: {q}"
    assert "6G research" in q, q
    assert "2024 election results" in q, q
    # only genuine label words are dropped, not any early colon
    assert _parse_queries("AI: alignment research", "raw")[0] == "AI: alignment research"
    assert _parse_queries("Tesla: Q3 earnings", "raw")[0] == "Tesla: Q3 earnings"
    assert _parse_queries("Query: real one", "raw")[0] == "real one"
    print("query parsing: markers stripped, digits/colons in queries preserved OK")


def test_source_sanitization():
    from server.research import _safe_md_title, _safe_md_url
    # a malicious search-result title trying to inject an image beacon
    evil = "Legit](https://ok) ![](https://evil.tld/b.png?ip=1"
    safe = _safe_md_title(evil)
    assert "[" not in safe and "]" not in safe, safe
    assert _safe_md_title("`code`") == "'code'"
    assert _safe_md_url("https://ok.example/p") == "https://ok.example/p"
    assert _safe_md_url("javascript:alert(1)") == ""
    assert _safe_md_url("https://evil/a) ![](b") == ""  # space/paren breakout blocked
    print("sanitization: title markdown neutralized, non-http/breakout urls dropped OK")


async def test_pipeline_happy_path():
    install_fakes()
    provider = ScriptedProvider(
        plan_text="greenhouse effect\nocean heat\ncarbon policy",
        report_text="Warming comes from greenhouse gases [1] and ocean heat [2].")
    conv, events = await run_research(provider)

    phases = [e["phase"] for e in events if e["type"] == "research_status"]
    for expected in ("planning", "searching", "reading", "synthesizing"):
        assert expected in phases, f"missing phase {expected}: {phases}"

    report = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert "greenhouse gases [1]" in report
    assert "### Sources" in report
    assert "https://a.example/greenhouse" in report
    assert events[-1]["type"] == "done"

    # persisted as a normal conversation: user question + assistant report
    msgs = await db.list_messages(conv["id"])
    assert [m["role"] for m in msgs] == ["assistant"]  # user msg added by the route, not the pipeline
    assert "### Sources" in msgs[0]["content"]
    print("pipeline: all phases, inline citations, sources list, persisted OK")


async def test_dedup_and_cap():
    # 12 distinct pages, but MAX_SOURCES caps how many are read
    pages = {f"https://s{i}.example/p": f"content number {i}" for i in range(12)}
    install_fakes(pages)
    provider = ScriptedProvider("q1\nq2", "report body")
    _, events = await run_research(provider)
    reading = [e for e in events if e["type"] == "research_status" and e["phase"] == "reading"]
    report = "".join(e["text"] for e in events if e["type"] == "text_delta")
    numbered = [ln for ln in report.splitlines() if ln.strip().startswith(tuple("0123456789"))]
    # never more sources than the cap, even though search returned 12 twice
    assert sum(1 for ln in numbered if "example" in ln) <= research_mod.MAX_SOURCES
    print(f"dedup + cap: read <= {research_mod.MAX_SOURCES} sources from 12 candidates OK")


async def test_fetch_failures_skipped():
    install_fakes(fail={"https://b.example/oceans"})
    provider = ScriptedProvider("q1", "report [1]")
    _, events = await run_research(provider)
    skips = [e["detail"] for e in events
             if e["type"] == "research_status" and "skipped" in e.get("detail", "")]
    assert any("b.example" in s for s in skips), skips
    report = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert "b.example/oceans" not in report, "a failed fetch must not appear in Sources"
    print("resilience: unreachable source skipped, kept out of the report OK")


class SynthErrorProvider(ModelProvider):
    """Plans normally, then fails synthesis - via an error event or a raise."""
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, mode="event"):
        super().__init__({})
        self.mode = mode
        self.calls = 0

    async def list_models(self):
        return [ModelInfo(id="fake-model")]

    async def chat(self, model, messages, tools=None, params=None):
        self.calls += 1
        if self.calls == 1:
            yield StreamEvent(type="text_delta", text="q1 ")
            yield StreamEvent(type="done")
            return
        if self.mode == "raise":
            raise RuntimeError("provider connection dropped")
        yield StreamEvent(type="error", message="context length exceeded")


async def test_synthesis_error_persists_honest_note():
    for mode in ("event", "raise"):
        install_fakes()
        provider = SynthErrorProvider(mode)
        conv, events = await run_research(provider)
        assert any(e["type"] == "error" for e in events), f"{mode}: no error event"
        report = "".join(e["text"] for e in events if e["type"] == "text_delta")
        assert "could not be generated" in report, f"{mode}: no honest failure note"
        assert "### Sources" in report, f"{mode}: sources still listed"
        assert events[-1]["type"] == "done"
        # persisted message is the honest note, NOT a bare sources list
        msgs = await db.list_messages(conv["id"])
        assert msgs and "could not be generated" in msgs[0]["content"], mode
    print("synthesis error: honest note persisted (event + raise), not bare sources OK")


async def test_stale_generation_not_persisted():
    from server import runs
    install_fakes()
    conv = await db.create_conversation("inst", "fake-model", False)
    # simulate a rewind/delete landing mid-run: bump generation so the run's
    # captured gen goes stale and its write must be skipped
    stale_gen = runs.generation(conv["id"])
    runs.bump_generation(conv["id"])
    provider = ScriptedProvider("q1", "a report [1]")
    events = [ev async for ev in stream_research(conv, provider, "q?", gen=stale_gen)]
    assert events[-1]["type"] == "done"  # run completes without crashing
    msgs = await db.list_messages(conv["id"])
    assert msgs == [], "a stale (rewound) run must not resurrect a report"
    print("stale generation: report not persisted into a rewound conversation OK")


async def test_no_sources_graceful():
    from server.tools import web

    async def empty_search(query, limit=6):
        return []

    async def no_local(question):
        return []

    saved_search, saved_local = web.search_web, research_mod._knowledge_sources
    web.search_web = empty_search
    research_mod._knowledge_sources = no_local
    try:
        provider = ScriptedProvider("q1", "should never synthesize")
        conv, events = await run_research(provider)
        report = "".join(e["text"] for e in events if e["type"] == "text_delta")
        assert "couldn't gather any sources" in report
        assert provider.calls == 1, "must not synthesize with no sources"
        msgs = await db.list_messages(conv["id"])
        assert msgs and "couldn't gather" in msgs[0]["content"]
    finally:  # don't leak patches into the route test that runs next
        web.search_web = saved_search
        research_mod._knowledge_sources = saved_local
    print("no sources: graceful message, no synthesis call OK")


def test_route():
    from starlette.testclient import TestClient
    from server.main import app
    install_fakes()

    import server.routes.chat as chatmod
    # create_provider is synchronous in the real code - return the fake directly
    chatmod.create_provider = lambda t, c: ScriptedProvider("greenhouse\nocean", "Report [1][2].")

    client = TestClient(app)
    local = {"Host": "127.0.0.1:8040"}

    r = client.post("/api/research", headers=local, json={"provider_id": "p1", "model": "m"})
    assert r.status_code == 422  # missing question field

    r = client.post("/api/research", headers=local,
                    json={"provider_id": "missing", "model": "m", "question": "hi"})
    assert r.status_code == 400

    # seed a real provider instance through the API (no asyncio.run in-loop)
    inst = client.post("/api/providers", headers=local, json={
        "type_id": "openai_compat", "name": "P", "config": {"base_url": "http://x/v1"}}).json()

    body = {"provider_id": inst["id"], "model": "m", "question": "why warming?"}
    with client.stream("POST", "/api/research", headers=local, json=body) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert '"type": "meta"' in text or '"type":"meta"' in text
    assert "research_status" in text
    assert "### Sources" in text
    print("route: validation (422/400) + streaming report with sources OK")


async def main():
    test_ddg_unwrap()
    test_parse_queries()
    test_source_sanitization()
    await test_pipeline_happy_path()
    await test_dedup_and_cap()
    await test_fetch_failures_skipped()
    await test_synthesis_error_persists_honest_note()
    await test_stale_generation_not_persisted()
    await test_no_sources_graceful()
    await db.close_db()
    test_route()
    print("\nALL RESEARCH TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
