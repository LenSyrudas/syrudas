"""The /v1 hub is the only contract with software outside this repo.

Continue, aider and any openai-python script read what it sends. A provider
failure used to arrive as ordinary assistant content, with the stream still
closing on finish_reason "stop" and a [DONE] marker - so a backend that fell
over looked exactly like a short answer. Non-streaming had the same hole
whenever any text had already been produced.

Offline: a scripted fake provider stands in for the model.

Run: .venv\\Scripts\\python.exe scripts\\test_openai_hub.py
"""
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.getLogger("httpx").setLevel(logging.WARNING)

TMP = Path(tempfile.mkdtemp(prefix="syrudas-hub-"))

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.providers.base import ModelProvider  # noqa: E402
from server.routes import openai_api  # noqa: E402
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec  # noqa: E402


class FakeProvider(ModelProvider):
    type_id = "fake"
    display_name = "Fake"
    script: list[StreamEvent] = []

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model")]

    async def chat(self, model: str, messages: list[Message],
                   tools: Optional[list[ToolSpec]] = None,
                   params: Optional[GenParams] = None) -> AsyncIterator[StreamEvent]:
        for ev in type(self).script:
            yield ev
        yield StreamEvent(type="done")


def use(script: list[StreamEvent]) -> None:
    FakeProvider.script = script
    openai_api.create_provider = lambda type_id, config: FakeProvider({})


def sse_events(body: str) -> list[dict]:
    """Parse an SSE body into its JSON frames, ignoring the [DONE] sentinel."""
    out = []
    for line in body.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                out.append(json.loads(payload))
    return out


# the loopback Host guard rejects TestClient's default "testserver" hostname
LOCAL = {"Host": "127.0.0.1:8040"}


def client():
    from starlette.testclient import TestClient
    from server.main import app
    return TestClient(app)


async def seed(names: list[str]) -> None:
    for inst in await db.list_provider_instances():
        await db.delete_provider_instance(inst["id"])
    for n in names:
        await db.create_provider_instance("fake", n, {})


# --- tests ---

def test_a_streaming_failure_is_an_error_not_an_answer():
    use([StreamEvent(type="text_delta", text="partial "),
         StreamEvent(type="error", message="model crashed")])
    with client() as c:
        r = c.post("/v1/chat/completions",
                   headers=LOCAL,
                   json={"model": "fake/fake-model", "stream": True,
                         "messages": [{"role": "user", "content": "hi"}]})
    body = r.text
    frames = sse_events(body)

    assert any("error" in f for f in frames), f"no error frame: {frames}"
    err = next(f for f in frames if "error" in f)
    assert "model crashed" in err["error"]["message"]
    assert "[DONE]" not in body, "a failed stream must not claim to have finished"
    assert not any(
        ch.get("finish_reason") == "stop"
        for f in frames for ch in f.get("choices", [])
    ), f"a failure still reported finish_reason stop: {frames}"
    print("streaming failure: error frame, no [DONE], no finish stop OK")


def test_a_clean_stream_still_finishes_properly():
    use([StreamEvent(type="text_delta", text="all good")])
    with client() as c:
        r = c.post("/v1/chat/completions",
                   headers=LOCAL,
                   json={"model": "fake/fake-model", "stream": True,
                         "messages": [{"role": "user", "content": "hi"}]})
    body = r.text
    frames = sse_events(body)

    assert "[DONE]" in body, "a healthy stream must still terminate normally"
    assert any(ch.get("finish_reason") == "stop"
               for f in frames for ch in f.get("choices", []))
    assert not any("error" in f for f in frames)
    print("clean stream: finish_reason stop and [DONE], unchanged OK")


def test_a_failure_after_partial_text_is_not_a_success():
    use([StreamEvent(type="text_delta", text="half an ans"),
         StreamEvent(type="error", message="connection reset")])
    with client() as c:
        r = c.post("/v1/chat/completions",
                   headers=LOCAL,
                   json={"model": "fake/fake-model",
                         "messages": [{"role": "user", "content": "hi"}]})
    # previously `if error and not text_parts` let this through as a truncated
    # but successful completion, which a caller cannot distinguish
    assert r.status_code == 502, f"expected 502, got {r.status_code}: {r.text[:200]}"
    assert "connection reset" in r.text
    print("non-streaming failure after partial text: 502, not a short answer OK")


def test_a_bare_model_id_is_refused_when_ambiguous():
    import asyncio
    asyncio.get_event_loop_policy().new_event_loop()
    with client() as c:
        r = c.post("/v1/chat/completions",
                   headers=LOCAL,
                   json={"model": "fake-model",
                         "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 404, r.status_code
        assert "Ambiguous" in r.text, r.text
        # a qualified id still routes
        use([StreamEvent(type="text_delta", text="ok")])
        r2 = c.post("/v1/chat/completions",
                    headers=LOCAL,
                    json={"model": "second/fake-model",
                          "messages": [{"role": "user", "content": "hi"}]})
        assert r2.status_code == 200, r2.text[:200]
    print("bare id with several providers: refused; qualified id routes OK")


def test_an_unknown_provider_slug_is_refused():
    with client() as c:
        r = c.post("/v1/chat/completions",
                   headers=LOCAL,
                   json={"model": "nosuch/fake-model",
                         "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404, r.status_code
    assert "No provider named" in r.text, r.text
    print("unknown provider slug: 404 naming the slug, not a silent fallback OK")


def main() -> None:
    import asyncio

    async def prepare(names):
        await seed(names)
        await db.close_db()

    asyncio.run(prepare(["fake"]))
    test_a_streaming_failure_is_an_error_not_an_answer()
    test_a_clean_stream_still_finishes_properly()
    test_a_failure_after_partial_text_is_not_a_success()
    test_an_unknown_provider_slug_is_refused()

    asyncio.run(prepare(["fake", "second"]))
    test_a_bare_model_id_is_refused_when_ambiguous()
    print("\nALL OPENAI HUB TESTS PASSED")


if __name__ == "__main__":
    main()
