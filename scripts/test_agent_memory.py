"""Tests for agent memory: tools, prompt injection, caps, and REST routes.

Drives the real agent loop with a scripted fake provider against a temp
database - no network, no real LLM, and the user's data is untouched.

Run: .venv\\Scripts\\python.exe scripts\\test_agent_memory.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-memory-"))

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.agent import stream_agent_chat  # noqa: E402
from server.chat import stream_plain_chat  # noqa: E402
from server.providers.base import ModelProvider  # noqa: E402
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolSpec  # noqa: E402
from server.tools import memory as memory_mod  # noqa: E402
from server.tools.memory import (  # noqa: E402
    MemoryDeleteTool, MemorySaveTool, MemorySearchTool, memory_prompt_block,
)


class FakeProvider(ModelProvider):
    type_id = "fake"
    display_name = "Fake"

    def __init__(self):
        super().__init__({})
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
        yield StreamEvent(type="text_delta", text="ok")
        yield StreamEvent(type="done")


def system_text(provider: FakeProvider) -> str:
    msgs = provider.calls[0]["messages"]
    return msgs[0].content if msgs and msgs[0].role == "system" else ""


async def drive_agent(system_prompt: str = "") -> FakeProvider:
    conv = await db.create_conversation("inst", "fake-model", True, system_prompt)
    await db.add_message(conv["id"], "user", "hi")
    provider = FakeProvider()
    async for _ in stream_agent_chat(conv, provider):
        pass
    return provider


async def test_tools_roundtrip():
    save, delete, search = MemorySaveTool(), MemoryDeleteTool(), MemorySearchTool()

    r = await save.run({"content": "  User prefers   metric units  "})
    assert r.startswith("Saved memory ["), r
    mem_id = r.split("[")[1].rstrip("]")
    mems = await db.list_memories()
    assert len(mems) == 1 and mems[0]["content"] == "User prefers metric units"

    # exact duplicate reuses the row instead of piling up
    r2 = await save.run({"content": "User prefers metric units"})
    assert mem_id in r2 and len(await db.list_memories()) == 1

    r = await search.run({"query": "metric"})
    assert mem_id in r and "metric units" in r
    r = await search.run({"query": "100%_literal"})
    assert r.startswith("No memories"), "LIKE wildcards must be escaped"

    r = await delete.run({"id": f"[{mem_id}]"})  # brackets tolerated
    assert r.startswith("Deleted"), r
    assert await db.count_memories() == 0
    r = await delete.run({"id": mem_id})
    assert r.startswith("Error: no memory"), r
    print("memory tools: save/dedup/search/delete roundtrip OK")


async def test_caps():
    save = MemorySaveTool()
    r = await save.run({"content": "x" * (memory_mod.MAX_MEMORY_CHARS + 1)})
    assert r.startswith("Error: memory too long"), r
    r = await save.run({"content": "   "})
    assert r.startswith("Error: empty"), r

    old_max = memory_mod.MAX_MEMORIES
    memory_mod.MAX_MEMORIES = 3
    try:
        for i in range(3):
            await save.run({"content": f"fact number {i}"})
        r = await save.run({"content": "one too many"})
        assert r.startswith("Error: memory is full"), r
        assert await db.count_memories() == 3
    finally:
        memory_mod.MAX_MEMORIES = old_max
        await db.clear_memories()
    print("memory caps: length, empty, and count limits enforced OK")


async def test_agent_prompt_injection():
    await db.add_memory("User's project is called Syrudas")

    provider = await drive_agent()
    sys_text = system_text(provider)
    assert "Saved memories" in sys_text and "project is called Syrudas" in sys_text
    assert "memory_save" in sys_text, "guideline about saving memories missing"
    offered = {t.name for t in provider.calls[0]["tools"]}
    assert {"memory_save", "memory_delete", "memory_search"} <= offered
    from server.tools import builtin_tools
    assert not any(t.requires_approval for t in builtin_tools()
                   if t.name.startswith("memory_")), "memory tools must be ungated"

    # custom persona conversations still get memories appended after the persona
    provider = await drive_agent(system_prompt="You are a pirate.")
    sys_text = system_text(provider)
    assert sys_text.startswith("You are a pirate.")
    assert "project is called Syrudas" in sys_text

    # memories are request-local: nothing baked into the stored conversation
    convs = await db.list_conversations()
    assert all("Saved memories" not in (c["system_prompt"] or "") for c in convs)
    await db.clear_memories()
    print("agent prompt: memories injected (default + persona), never persisted OK")


async def test_plain_chat_never_sees_memories():
    await db.add_memory("This must stay out of plain chat")
    conv = await db.create_conversation("inst", "fake-model", False, "Be terse.")
    await db.add_message(conv["id"], "user", "hi")
    provider = FakeProvider()
    async for _ in stream_plain_chat(conv, provider):
        pass
    sys_text = system_text(provider)
    assert "must stay out of plain chat" not in sys_text
    assert provider.calls[0]["tools"] is None
    await db.clear_memories()
    print("plain chat: no memories, no tools OK")


async def test_prompt_budget():
    old_budget = memory_mod.PROMPT_BUDGET_CHARS
    memory_mod.PROMPT_BUDGET_CHARS = 120
    try:
        for i in range(8):
            await db.add_memory(f"memory number {i} " + "pad " * 10)
        block = await memory_prompt_block()
        assert "memory number 7" in block, "newest memory must always be shown"
        assert "older memories not shown" in block
        assert "memory_search" in block
    finally:
        memory_mod.PROMPT_BUDGET_CHARS = old_budget
        await db.clear_memories()
    print("prompt budget: newest-first slice with overflow note OK")


def test_routes():
    from starlette.testclient import TestClient
    from server.main import app

    client = TestClient(app)
    local = {"Host": "127.0.0.1:8040"}

    r = client.post("/api/memories", headers=local, json={"content": "from the UI"})
    assert r.status_code == 200, r.text
    mem_id = r.json()["id"]

    r = client.get("/api/memories", headers=local)
    assert [m["id"] for m in r.json()] == [mem_id]

    r = client.post("/api/memories", headers=local, json={"content": "x" * 501})
    assert r.status_code == 400
    r = client.post("/api/memories", headers=local, json={"content": "  "})
    assert r.status_code == 400

    r = client.delete(f"/api/memories/{mem_id}", headers=local)
    assert r.status_code == 200 and r.json()["ok"] is True
    r = client.delete(f"/api/memories/{mem_id}", headers=local)
    assert r.status_code == 404

    client.post("/api/memories", headers=local, json={"content": "a"})
    client.post("/api/memories", headers=local, json={"content": "b"})
    r = client.delete("/api/memories", headers=local)
    assert r.status_code == 200 and r.json()["deleted"] == 2
    print("routes: add/list/delete/clear with validation OK")


async def main():
    await test_tools_roundtrip()
    await test_caps()
    await test_agent_prompt_injection()
    await test_plain_chat_never_sees_memories()
    await test_prompt_budget()
    # TestClient drives the app in its own event loop - hand the connection over
    await db.close_db()
    test_routes()
    print("\nALL AGENT MEMORY TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
