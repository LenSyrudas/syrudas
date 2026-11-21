"""Auto mode: gated tools run without the per-call approval prompt.

The approval gate is what keeps a shell command or an out-of-workspace write
from executing unreviewed. Auto mode trades that review for autonomy, so the
two things that must be provably true are:

- with auto_approve on, a gated tool executes immediately, no approval event
  is emitted, and the tool_result is marked as auto-approved so the UI can
  show the call went through unreviewed;
- with it off (the default), the same call still parks on an approval, and
  nothing executes until the user says so.

Run: .venv\\Scripts\\python.exe scripts\\test_auto_approve.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-auto-"))
WORKSPACE = TMP / "workspace"
WORKSPACE.mkdir()

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.tools import files as files_mod  # noqa: E402
files_mod.DEFAULT_WORKSPACE = WORKSPACE

from server import agent  # noqa: E402
from server.agent import stream_agent_chat  # noqa: E402
from server.providers.base import ModelProvider  # noqa: E402
from server.schemas import ModelInfo, StreamEvent, ToolCall  # noqa: E402
from server.tools import Tool  # noqa: E402


class GatedTool(Tool):
    name = "gated"
    description = "a tool that always needs approval"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    requires_approval = True

    def __init__(self):
        self.runs = 0

    async def run(self, args: dict[str, Any]) -> str:
        self.runs += 1
        return f"ran with {args['value']}"


class FakeProvider(ModelProvider):
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, turns):
        super().__init__({})
        self.turns = list(turns)
        self.seen_system = ""

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model")]

    async def chat(self, model, messages, tools=None, params=None) -> AsyncIterator[StreamEvent]:
        if messages and messages[0].role == "system":
            self.seen_system = messages[0].content
        turn = self.turns.pop(0) if self.turns else [StreamEvent(type="text_delta", text="done")]
        for ev in turn:
            yield ev
        yield StreamEvent(type="done")


def use(*tools: Tool) -> None:
    async def collect():
        return list(tools)
    agent.collect_tools = collect


def one_call() -> list[StreamEvent]:
    return [StreamEvent(type="tool_call",
                        tool_call=ToolCall(id="c1", name="gated", arguments={"value": "x"}))]


async def make_conv(auto_approve: bool = False) -> dict:
    conv = await db.create_conversation("i", "fake-model", agent_mode=True,
                                        auto_approve=auto_approve)
    await db.add_message(conv["id"], "user", "go")
    return await db.get_conversation(conv["id"])


async def test_auto_mode_runs_gated_tools_without_prompting():
    tool = GatedTool()
    use(tool)
    conv = await make_conv()
    provider = FakeProvider([one_call()])
    events = [ev async for ev in stream_agent_chat(conv, provider, auto_approve=True)]

    assert tool.runs == 1, f"the gated tool should have run once, ran {tool.runs}"
    assert not any(ev.get("type") == "approval_required" for ev in events), \
        "auto mode must not emit approval prompts"
    results = [ev for ev in events if ev.get("type") == "tool_result"]
    assert results and all(ev.get("auto_approved") for ev in results), \
        f"the result should be marked auto-approved for the UI: {results}"
    assert results[0].get("content") == "ran with x", results
    print("auto mode: gated tool ran immediately, marked auto-approved OK")


async def test_default_mode_still_parks_on_approval():
    tool = GatedTool()
    use(tool)
    conv = await make_conv()
    provider = FakeProvider([one_call()])
    approvals = []
    results = []
    async for ev in stream_agent_chat(conv, provider):
        if ev.get("type") == "approval_required":
            approvals.append(ev)
            assert tool.runs == 0, "the tool ran before the user answered"
            agent.resolve_approval(ev["approval_id"], True)
        elif ev.get("type") == "tool_result":
            results.append(ev)

    assert approvals, "without auto mode the run must ask before executing"
    assert tool.runs == 1, "after approval the tool should run exactly once"
    assert results and not results[0].get("auto_approved"), \
        "a manually approved call must not wear the auto marker"
    print("default mode: parked on approval, ran only after it, no auto marker OK")


async def test_auto_mode_tells_the_model_prompts_are_off():
    use(GatedTool())
    conv = await make_conv()
    provider = FakeProvider([[StreamEvent(type="text_delta", text="hi")]])
    async for _ in stream_agent_chat(conv, provider, auto_approve=True):
        pass
    assert "Auto mode is on" in provider.seen_system, \
        "the model should be told gated tools no longer wait for approval"

    provider2 = FakeProvider([[StreamEvent(type="text_delta", text="hi")]])
    async for _ in stream_agent_chat(conv, provider2):
        pass
    assert "Auto mode is on" not in provider2.seen_system, \
        "the default prompt must keep describing the approval gate"
    print("system prompt: auto-mode note present only when the mode is on OK")


async def test_auto_approve_is_stored_on_the_conversation():
    conv = await make_conv(auto_approve=True)
    assert conv["auto_approve"] == 1, f"expected 1, got {conv['auto_approve']!r}"
    await db.update_conversation(conv["id"], auto_approve=0)
    conv = await db.get_conversation(conv["id"])
    assert conv["auto_approve"] == 0, "update_conversation should flip it back"
    print("persistence: auto_approve round-trips through the conversations row OK")


async def test_flipping_auto_on_mid_run_skips_the_next_prompt():
    """The toggle PATCHes the conversation row; the loop must re-read it."""
    tool = GatedTool()
    use(tool)
    conv = await make_conv()  # auto_approve=0 on the row
    turns = [
        [StreamEvent(type="tool_call",
                     tool_call=ToolCall(id="c1", name="gated", arguments={"value": "1"}))],
        [StreamEvent(type="tool_call",
                     tool_call=ToolCall(id="c2", name="gated", arguments={"value": "2"}))],
    ]
    provider = FakeProvider(turns)
    prompts = 0
    async for ev in stream_agent_chat(conv, provider):
        if ev.get("type") == "approval_required":
            prompts += 1
            assert ev["tool_call"]["id"] == "c1", "the second call should not have asked"
            # the user flips Auto on, then answers the prompt already on screen
            await db.update_conversation(conv["id"], auto_approve=1)
            agent.resolve_approval(ev["approval_id"], True)

    assert prompts == 1, f"expected exactly one prompt, got {prompts}"
    assert tool.runs == 2, f"both calls should have run, got {tool.runs}"
    print("mid-run toggle: first call asked, second ran under the new setting OK")


async def test_the_auto_marker_survives_a_reload():
    tool = GatedTool()
    use(tool)
    conv = await make_conv(auto_approve=True)
    provider = FakeProvider([one_call()])
    async for _ in stream_agent_chat(conv, provider, auto_approve=True):
        pass

    rows = await db.list_messages(conv["id"])
    tool_rows = [r for r in rows if r["role"] == "tool"]
    assert tool_rows and tool_rows[0]["auto_approved"] == 1, \
        f"the stored row must keep the marker: {tool_rows}"
    print("persistence: the tool row keeps auto_approved=1 for the reloaded UI OK")


async def main():
    try:
        await test_auto_mode_runs_gated_tools_without_prompting()
        await test_default_mode_still_parks_on_approval()
        await test_auto_mode_tells_the_model_prompts_are_off()
        await test_auto_approve_is_stored_on_the_conversation()
        await test_flipping_auto_on_mid_run_skips_the_next_prompt()
        await test_the_auto_marker_survives_a_reload()
    finally:
        await db.close_db()
    print("\nALL AUTO-APPROVE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
