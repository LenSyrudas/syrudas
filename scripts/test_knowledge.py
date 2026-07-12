"""Tests for the knowledge (local RAG) layer: chunking, indexing, search,
the agent tool, prompt block, and REST routes.

Uses a deterministic fake embedder (bag-of-words over hash buckets) so cosine
ranking is meaningful without any network or real model. Temp DB + temp
workspace - the user's data is untouched.

Run: .venv\\Scripts\\python.exe scripts\\test_knowledge.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-knowledge-"))
WORKSPACE = TMP / "workspace"
WORKSPACE.mkdir()

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.tools import files as files_mod  # noqa: E402
files_mod.DEFAULT_WORKSPACE = WORKSPACE

from server import knowledge as knowledge_mod  # noqa: E402
from server.agent import stream_agent_chat  # noqa: E402
from server.knowledge import chunk_text, normalize, pack, unpack  # noqa: E402
from server.providers.base import ModelProvider  # noqa: E402
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec  # noqa: E402
from server.tools.knowledge import KnowledgeSearchTool, knowledge_prompt_block  # noqa: E402

DIM = 32


def fake_vec(text: str) -> list[float]:
    v = [0.0] * DIM
    for tok in text.lower().split():
        v[hash(tok) % DIM] += 1.0
    return v


class FakeEmbedder(ModelProvider):
    type_id = "fake_embed"
    display_name = "Fake embedder"

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-embed")]

    async def chat(self, model, messages, tools=None, params=None):  # pragma: no cover
        yield StreamEvent(type="done")

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return [fake_vec(t) for t in texts]


async def fake_get_embedder():
    return FakeEmbedder({}), "fake-embed"


knowledge_mod.get_embedder = fake_get_embedder


def text_turn(text):
    return [StreamEvent(type="text_delta", text=text)]


def tool_turn(name, args, call_id="tc1"):
    return [StreamEvent(type="tool_call",
                        tool_call=ToolCall(id=call_id, name=name, arguments=args))]


class FakeChatProvider(ModelProvider):
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, turns=None):
        super().__init__({})
        self.turns = list(turns) if turns else None
        self.calls: list[dict] = []

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model")]

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        params: Optional[GenParams] = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append({"messages": list(messages), "tools": tools})
        turn = self.turns.pop(0) if self.turns else text_turn("ok")
        for ev in turn:
            yield ev
        yield StreamEvent(type="done")


def test_chunking():
    assert chunk_text("") == []
    assert chunk_text("short text") == ["short text"]

    text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(12))
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= knowledge_mod.CHUNK_CHARS for c in chunks)
    # every paragraph marker survives chunking somewhere
    for i in range(12):
        assert any(f"Paragraph {i}" in c for c in chunks), f"paragraph {i} lost"

    # pathological: one giant token cannot loop forever or produce empties
    chunks = chunk_text("x" * 5000)
    assert chunks and all(c for c in chunks)

    vec = [3.0, 4.0]
    assert abs(sum(x * x for x in normalize(vec)) - 1.0) < 1e-6
    assert list(unpack(pack([0.5, -1.5]))) == [0.5, -1.5]
    print("chunking + vector helpers OK")


async def test_indexing():
    (WORKSPACE / "bread.md").write_text(
        "Banana bread needs ripe banana, flour, butter and an oven.", "utf-8")
    (WORKSPACE / "physics.txt").write_text(
        "Quantum flux capacitors resonate at tachyon frequencies.", "utf-8")
    (WORKSPACE / "binary.exe.log").write_bytes(b"text\x00null")
    (WORKSPACE / "image.png").write_bytes(b"\x89PNG")  # extension not indexable

    result = await knowledge_mod.index_path(str(WORKSPACE))
    paths = [i["path"] for i in result["indexed"]]
    assert any("bread.md" in p for p in paths), result
    assert any("physics.txt" in p for p in paths), result
    assert any("binary.exe.log" in s for s in result["skipped"]), "binary must be skipped"
    assert not any("image.png" in p for p in paths), "non-text extension must be ignored"

    sources = await db.list_knowledge_sources()
    assert len(sources) == 2
    # reindexing the same folder must replace, not duplicate
    await knowledge_mod.index_path(str(WORKSPACE))
    assert len(await db.list_knowledge_sources()) == 2

    try:
        await knowledge_mod.index_path("C:\\Windows\\System32")
        raise AssertionError("indexing outside the sandbox must be refused")
    except ValueError as exc:
        assert "not in an allowed folder" in str(exc).lower() or "escapes" in str(exc).lower()
    print("indexing: folder walk, binary skip, reindex-no-dup, sandbox OK")


async def test_search_ranking():
    hits = await knowledge_mod.search("banana bread flour")
    assert hits and "bread.md" in hits[0]["path"], hits
    hits = await knowledge_mod.search("quantum tachyon frequencies")
    assert hits and "physics.txt" in hits[0]["path"], hits

    # chunks embedded under a different-dimension model are skipped, not crashed
    await db.replace_knowledge_source(
        "stale-model-source", "file", 10, [("old dims", pack([1.0, 2.0]))])
    hits = await knowledge_mod.search("banana bread flour")
    assert all(h["path"] != "stale-model-source" for h in hits)
    await db.clear_knowledge()
    assert await db.count_knowledge_chunks() == 0
    print("search: ranking by topic, dim-mismatch skip, clear OK")


async def test_tool_and_agent_loop():
    tool = KnowledgeSearchTool()

    r = await tool.run({"query": "  "})
    assert r.startswith("Error: empty"), r
    r = await tool.run({"query": "anything"})
    assert "index is empty" in r, r
    assert await knowledge_prompt_block() == ""

    (WORKSPACE / "bread.md").write_text(
        "Banana bread needs ripe banana, flour, butter and an oven.", "utf-8")
    await knowledge_mod.index_path(str(WORKSPACE / "bread.md"))

    r = await tool.run({"query": "banana flour", "k": "not-a-number"})
    assert "bread.md" in r and "Banana bread" in r, r

    block = await knowledge_prompt_block()
    assert "bread.md" in block and "knowledge_search" in block

    conv = await db.create_conversation("inst", "fake-model", True)
    await db.add_message(conv["id"], "user", "what goes in banana bread?")
    provider = FakeChatProvider(turns=[
        tool_turn("knowledge_search", {"query": "banana bread"}),
        text_turn("done"),
    ])
    events = [ev async for ev in stream_agent_chat(conv, provider)]
    assert not any(e["type"] == "approval_required" for e in events), \
        "knowledge_search must be ungated"
    result = next(e for e in events if e["type"] == "tool_result")
    assert "Banana bread" in result["content"], result
    sys_text = provider.calls[0]["messages"][0].content
    assert "local knowledge index" in sys_text and "bread.md" in sys_text

    # unconfigured embedder surfaces as a readable tool error, not a crash
    async def broken():
        raise ValueError("No embedding model configured (Settings > Knowledge)")
    old = knowledge_mod.get_embedder
    knowledge_mod.get_embedder = broken
    try:
        r = await tool.run({"query": "anything"})
        assert r.startswith("Error: No embedding model configured"), r
    finally:
        knowledge_mod.get_embedder = old
    await db.clear_knowledge()
    print("tool + agent loop: ungated dispatch, prompt block, config errors OK")


def test_routes():
    from starlette.testclient import TestClient
    from server.main import app

    async def fake_probe(provider_id, model):
        if provider_id != "p-ok":
            raise ValueError("Provider instance not found")
        return DIM
    knowledge_mod.probe = fake_probe

    client = TestClient(app)
    local = {"Host": "127.0.0.1:8040"}

    r = client.get("/api/knowledge", headers=local)
    assert r.status_code == 200 and r.json()["embedding"] is None

    r = client.put("/api/knowledge/embedding", headers=local,
                   json={"provider_id": "p-bad", "model": "m"})
    assert r.status_code == 400
    r = client.put("/api/knowledge/embedding", headers=local,
                   json={"provider_id": "p-ok", "model": "fake-embed"})
    assert r.status_code == 200 and r.json()["dim"] == DIM

    (WORKSPACE / "route.md").write_text("Routing tests eat banana bread too.", "utf-8")
    r = client.post("/api/knowledge/index", headers=local,
                    json={"path": str(WORKSPACE / "route.md")})
    assert r.status_code == 200 and r.json()["indexed"], r.text
    r = client.post("/api/knowledge/index", headers=local,
                    json={"path": "C:\\Windows\\System32"})
    assert r.status_code == 400

    r = client.post("/api/knowledge/search", headers=local, json={"query": "banana"})
    assert r.status_code == 200 and r.json()["results"], r.text

    source_id = client.get("/api/knowledge", headers=local).json()["sources"][0]["id"]
    r = client.delete(f"/api/knowledge/sources/{source_id}", headers=local)
    assert r.status_code == 200
    r = client.delete(f"/api/knowledge/sources/{source_id}", headers=local)
    assert r.status_code == 404
    r = client.delete("/api/knowledge", headers=local)
    assert r.status_code == 200
    print("routes: config validation, index, search, delete, sandbox 400 OK")


async def main():
    test_chunking()
    await test_indexing()
    await test_search_ranking()
    await test_tool_and_agent_loop()
    # TestClient drives the app in its own event loop - hand the connection over
    await db.close_db()
    test_routes()
    print("\nALL KNOWLEDGE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
