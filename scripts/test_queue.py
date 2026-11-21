"""Queued follow-ups: a message sent while a run is active waits its turn.

Two things must be provably true for this to be safe:

- the queued request does NOT run concurrently with the active one (the
  per-conversation lock, not luck, is what serializes them), and
- its user message lands in history AFTER the active run's reply, not
  interleaved into the middle of it.

A blocking fake provider holds the first run open while the second request
arrives, so both properties are observed rather than assumed.

Run: .venv\\Scripts\\python.exe scripts\\test_queue.py
"""
import asyncio
import json
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.getLogger("httpx").setLevel(logging.WARNING)

TMP = Path(tempfile.mkdtemp(prefix="syrudas-queue-"))
WORKSPACE = TMP / "workspace"
WORKSPACE.mkdir()

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.tools import files as files_mod  # noqa: E402
files_mod.DEFAULT_WORKSPACE = WORKSPACE

from server.providers.base import ModelProvider  # noqa: E402
from server.routes import chat as chat_route  # noqa: E402
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec  # noqa: E402


class BlockingProvider(ModelProvider):
    """First call holds until released; later calls answer immediately."""

    type_id = "fake"
    display_name = "Fake"
    release = threading.Event()
    entered = threading.Event()
    calls = 0
    concurrent = 0          # peak number of chat() bodies alive at once
    _live = 0
    _lock = threading.Lock()

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model")]

    async def chat(self, model: str, messages: list[Message],
                   tools: Optional[list[ToolSpec]] = None,
                   params: Optional[GenParams] = None) -> AsyncIterator[StreamEvent]:
        with type(self)._lock:
            type(self)._live += 1
            type(self).concurrent = max(type(self).concurrent, type(self)._live)
            type(self).calls += 1
            n = type(self).calls
        try:
            if n == 1:
                type(self).entered.set()
                while not type(self).release.is_set():
                    await asyncio.sleep(0.01)
                yield StreamEvent(type="text_delta", text="first reply")
            else:
                yield StreamEvent(type="text_delta", text="second reply")
            yield StreamEvent(type="done")
        finally:
            with type(self)._lock:
                type(self)._live -= 1


LOCAL = {"Host": "127.0.0.1:8040"}  # the loopback Host guard rejects "testserver"


def client():
    from starlette.testclient import TestClient
    from server.main import app
    return TestClient(app)


def ndjson_texts(body: str) -> list[str]:
    return [json.loads(line).get("text", "")
            for line in body.splitlines() if line.strip()]


async def seed() -> str:
    for inst in await db.list_provider_instances():
        await db.delete_provider_instance(inst["id"])
    inst = await db.create_provider_instance("fake", "fake", {})
    chat_route.create_provider = lambda type_id, config: BlockingProvider({})
    return inst["id"]


def test_a_queued_followup_waits_and_lands_in_order():
    provider_id = asyncio.run(seed())
    results: dict[str, str] = {}

    # one client, one portal, one event loop - asyncio primitives (the queue
    # lock) are loop-bound, and two TestClients would fake a two-process race
    # the real single-process app never has
    with client() as c:
        def send(payload: dict, key: str) -> None:
            r = c.post("/api/chat", headers=LOCAL, json=payload)
            results[key] = f"{r.status_code}\n{r.text}"

        first = threading.Thread(target=send, args=(
            {"provider_id": provider_id, "model": "fake-model", "message": "one"}, "first"))
        first.start()
        assert BlockingProvider.entered.wait(timeout=10), "the first run never started"

        conv_id = asyncio.run(db.list_conversations())[0]["id"]
        queued = threading.Thread(target=send, args=(
            {"provider_id": provider_id, "model": "fake-model", "message": "two",
             "conversation_id": conv_id, "queue": True}, "queued"))
        queued.start()

        time.sleep(1.0)  # long enough that a wrongly-concurrent run would show
        assert queued.is_alive(), "the queued request answered while the first was still running"
        assert BlockingProvider.concurrent == 1, \
            f"two runs were alive at once (peak {BlockingProvider.concurrent})"

        BlockingProvider.release.set()
        first.join(timeout=15)
        queued.join(timeout=15)
        assert not first.is_alive() and not queued.is_alive(), "a request hung"

    assert "first reply" in results["first"], results["first"][:300]
    assert "second reply" in results["queued"], results["queued"][:300]

    roles = [(m["role"], m["content"]) for m in asyncio.run(db.list_messages(conv_id))]
    summary = [(r, c[:12]) for r, c in roles]
    order = [r for r, _ in roles]
    assert order == ["user", "assistant", "user", "assistant"], \
        f"the follow-up interleaved with the active run: {summary}"
    assert roles[1][1] == "first reply" and roles[3][1] == "second reply", summary
    print("queued follow-up: waited, ran second, history in order OK")


def test_an_unqueued_send_is_still_refused():
    provider_id = asyncio.run(seed())
    BlockingProvider.release.clear()
    BlockingProvider.entered.clear()
    BlockingProvider.calls = 0
    BlockingProvider.concurrent = 0
    results: dict[str, str] = {}

    with client() as c:
        def send(payload: dict, key: str) -> None:
            r = c.post("/api/chat", headers=LOCAL, json=payload)
            results[key] = f"{r.status_code}"

        first = threading.Thread(target=send, args=(
            {"provider_id": provider_id, "model": "fake-model", "message": "uno"}, "first"))
        first.start()
        assert BlockingProvider.entered.wait(timeout=10)

        conv_id = [x for x in asyncio.run(db.list_conversations()) if x["title"] == "uno"][0]["id"]
        send({"provider_id": provider_id, "model": "fake-model", "message": "dos",
              "conversation_id": conv_id}, "refused")  # no queue flag: main thread

        BlockingProvider.release.set()
        first.join(timeout=15)

    assert results["refused"].startswith("409"), \
        f"an unqueued send during a run must be refused: {results}"
    print("unqueued send during a run: still a 409 OK")


if __name__ == "__main__":
    try:
        test_a_queued_followup_waits_and_lands_in_order()
        test_an_unqueued_send_is_still_refused()
    finally:
        asyncio.run(db.close_db())
    print("\nALL QUEUE TESTS PASSED")
