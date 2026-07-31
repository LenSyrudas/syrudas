"""History is sized from the model's context window, and re-trimmed every step.

A fixed character budget is wrong in both directions at once: it wastes most of
a 32k-token window, and it can still overrun a small one. These tests pin the
sizing, the fallbacks when a backend will not say, and the behaviour that
actually bites - a tool-heavy agent turn growing past the window mid-run, at
which point the backend starts dropping the system prompt and the user's
request.

Offline: a scripted fake provider stands in for the model and reports whatever
context window each test needs.

Run: .venv\\Scripts\\python.exe scripts\\test_context_budget.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-budget-"))
WORKSPACE = TMP / "workspace"
WORKSPACE.mkdir()

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.tools import files as files_mod  # noqa: E402
files_mod.DEFAULT_WORKSPACE = WORKSPACE

from server import agent  # noqa: E402
from server.agent import stream_agent_chat  # noqa: E402
from server.chat import build_history, history_budget, trim_history  # noqa: E402
from server.config import (  # noqa: E402
    CHARS_PER_TOKEN, MAX_HISTORY_CHARS, RESPONSE_RESERVE_TOKENS,
    TOOL_SCHEMA_RESERVE_TOKENS,
)
from server.providers.base import ModelProvider  # noqa: E402
from server.providers.openai_compat import _context_from_entry  # noqa: E402
from server.schemas import (  # noqa: E402
    GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec)
from server.tools import Tool  # noqa: E402


class FakeProvider(ModelProvider):
    """Reports a chosen context window and records what it was sent."""
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, context: Optional[int] = None, turns=None, raises=False):
        super().__init__({})
        self.context = context
        self.raises = raises
        self.turns = list(turns or [])
        self.seen: list[list[Message]] = []

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model", context_tokens=self.context)]

    async def context_tokens(self, model: str) -> Optional[int]:
        if self.raises:
            raise RuntimeError("backend unreachable")
        return self.context

    async def chat(self, model: str, messages: list[Message],
                   tools: Optional[list[ToolSpec]] = None,
                   params: Optional[GenParams] = None) -> AsyncIterator[StreamEvent]:
        self.seen.append(list(messages))
        turn = self.turns.pop(0) if self.turns else [
            StreamEvent(type="text_delta", text="done")]
        for ev in turn:
            yield ev
        yield StreamEvent(type="done")


class BigOutputTool(Tool):
    """Returns a large result, the way a file read or a shell dump does."""
    name = "big"
    description = "returns a lot of text"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    CHUNK = 12_000

    async def run(self, args: dict[str, Any]) -> str:
        return "X" * self.CHUNK


def chars(messages: list[Message]) -> int:
    return sum(len(m.content or "") for m in messages)


async def make_conv(text="the original request, which must survive") -> dict:
    conv = await db.create_conversation("inst", "fake-model", agent_mode=True)
    await db.add_message(conv["id"], "user", text)
    return await db.get_conversation(conv["id"])


# --- tests ---

async def test_budget_scales_with_the_window():
    small = await history_budget(FakeProvider(context=8_192), "fake-model")
    large = await history_budget(FakeProvider(context=32_768), "fake-model")
    huge = await history_budget(FakeProvider(context=128_000), "fake-model")

    assert small < large < huge, f"budget must track the window: {small}, {large}, {huge}"
    expected = int((32_768 - RESPONSE_RESERVE_TOKENS) * CHARS_PER_TOKEN)
    assert large == expected, f"expected {expected} chars for a 32k window, got {large}"
    assert large > MAX_HISTORY_CHARS * 4, \
        "a 32k window should buy far more than the fixed fallback"
    print(f"budget scales: 8k->{small:,}  32k->{large:,}  128k->{huge:,} chars OK")


async def test_agent_mode_reserves_room_for_tool_schemas():
    plain = await history_budget(FakeProvider(context=32_768), "fake-model")
    agent_mode = await history_budget(FakeProvider(context=32_768), "fake-model",
                                      agent_mode=True)
    assert agent_mode < plain, "agent mode must hold back room for the tool schemas"
    assert plain - agent_mode == int(TOOL_SCHEMA_RESERVE_TOKENS * CHARS_PER_TOKEN)
    print(f"agent mode reserves {plain - agent_mode:,} chars for tool schemas OK")


async def test_falls_back_when_the_window_is_unknown():
    assert await history_budget(FakeProvider(context=None), "m") == MAX_HISTORY_CHARS
    assert await history_budget(FakeProvider(raises=True), "m") == MAX_HISTORY_CHARS
    # a tiny window must not produce a budget below the fallback
    assert await history_budget(FakeProvider(context=512), "m") == MAX_HISTORY_CHARS
    print("unknown, failing and tiny windows all fall back to the fixed budget OK")


async def test_a_probe_failure_cannot_fail_a_turn():
    conv = await make_conv()
    provider = FakeProvider(raises=True)
    events = [ev async for ev in stream_agent_chat(conv, provider)]
    assert any(e["type"] == "done" for e in events), "a failed probe must not break the turn"
    print("a context probe that raises does not fail the turn OK")


async def test_history_is_retrimmed_every_step():
    """The behaviour that actually bites: growth *within* one turn."""
    async def only_big():
        return [BigOutputTool()]
    agent.collect_tools = only_big

    conv = await make_conv()
    calls = [
        [StreamEvent(type="tool_call",
                     tool_call=ToolCall(id=f"c{i}", name="big", arguments={}))]
        for i in range(6)
    ]
    provider = FakeProvider(context=8_192, turns=calls)
    async for _ in stream_agent_chat(conv, provider):
        pass

    budget = await history_budget(FakeProvider(context=8_192), "fake-model", agent_mode=True)
    sizes = [chars(m) for m in provider.seen]
    assert len(sizes) > 3, f"expected several steps, got {len(sizes)}"
    # 6 steps x 12k of tool output is 72k; without a per-step trim the request
    # grows without bound, so the ceiling is the whole point
    ceiling = budget + BigOutputTool.CHUNK * 2
    assert max(sizes) <= ceiling, \
        f"request grew to {max(sizes):,} chars against a {budget:,} budget: {sizes}"
    assert sizes[-1] < sum(len('X' * BigOutputTool.CHUNK) for _ in range(6)), \
        "history was never trimmed inside the loop"
    print(f"per-step trim: {len(sizes)} steps, largest request {max(sizes):,} chars "
          f"against a {budget:,} budget OK")


async def test_the_original_request_survives_a_bigger_window():
    """The 24k fallback evicted it after two tool results; 32k must not."""
    conv = await make_conv()
    await db.add_message(
        conv["id"], "assistant", "",
        tool_calls=[ToolCall(id="t1", name="big", arguments={}).model_dump()])
    await db.add_message(conv["id"], "tool", "A" * 12_000, tool_call_id="t1")
    await db.add_message(
        conv["id"], "assistant", "",
        tool_calls=[ToolCall(id="t2", name="big", arguments={}).model_dump()])
    await db.add_message(conv["id"], "tool", "B" * 12_000, tool_call_id="t2")
    conv = await db.get_conversation(conv["id"])

    narrow = await build_history(conv, MAX_HISTORY_CHARS)
    wide = await build_history(
        conv, await history_budget(FakeProvider(context=32_768), "fake-model"))

    assert not any(m.role == "user" for m in narrow), \
        "precondition: the fixed budget is what used to evict the request"
    assert any(m.role == "user" for m in wide), \
        "a 32k window must keep the user's original request"
    print("original request: evicted at 24k, kept once the real window is known OK")


def test_openrouter_context_is_read():
    assert _context_from_entry({"id": "m", "context_length": 128_000}) == 128_000
    assert _context_from_entry({"id": "m", "top_provider": {"context_length": 64_000}}) == 64_000
    assert _context_from_entry({"id": "m"}) is None
    assert _context_from_entry({"id": "m", "context_length": 0}) is None
    print("model-listing context lengths parsed, absent ones left as None OK")


async def main():
    test_openrouter_context_is_read()
    await test_budget_scales_with_the_window()
    await test_agent_mode_reserves_room_for_tool_schemas()
    await test_falls_back_when_the_window_is_unknown()
    await test_a_probe_failure_cannot_fail_a_turn()
    await test_history_is_retrimmed_every_step()
    await test_the_original_request_survives_a_bigger_window()
    await db.close_db()
    print("\nALL CONTEXT BUDGET TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
