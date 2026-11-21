"""Per-tool auto-approve rules and pause/resume for agent runs.

Per-tool rules are the narrow alternative to Auto mode: a tool the user
trusted once ("Always allow", or Settings) runs without asking even when the
conversation is not in auto mode - and every OTHER gated tool must still ask.

Pause is cooperative: the loop parks at the next step boundary, not mid-token.
The tests pin the semantics that make it safe - resume un-parks exactly the
run that parked, resuming nothing is a refusal, and Stop while parked tears
down cleanly with no waiter leaked.

Run: .venv\\Scripts\\python.exe scripts\\test_run_control.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-runctl-"))
WORKSPACE = TMP / "workspace"
WORKSPACE.mkdir()

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.tools import files as files_mod  # noqa: E402
files_mod.DEFAULT_WORKSPACE = WORKSPACE

from server import agent  # noqa: E402
from server.agent import drain_detached_writes, stream_agent_chat  # noqa: E402
from server.providers.base import ModelProvider  # noqa: E402
from server.schemas import ModelInfo, StreamEvent, ToolCall  # noqa: E402
from server.tools import Tool  # noqa: E402


class GatedTool(Tool):
    requires_approval = True

    def __init__(self, name: str):
        self.name = name
        self.description = f"gated tool {name}"
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}
        self.runs = 0

    async def run(self, args: dict[str, Any]) -> str:
        self.runs += 1
        return f"{self.name} ran"


class FreeTool(Tool):
    name = "free"
    description = "never gated"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any]) -> str:
        return "free ran"


class FakeProvider(ModelProvider):
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, turns):
        super().__init__({})
        self.turns = list(turns)

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model")]

    async def chat(self, model, messages, tools=None, params=None) -> AsyncIterator[StreamEvent]:
        turn = self.turns.pop(0) if self.turns else [StreamEvent(type="text_delta", text="done")]
        for ev in turn:
            yield ev
        yield StreamEvent(type="done")


def use(*tools: Tool) -> None:
    async def collect():
        return list(tools)
    agent.collect_tools = collect


def call(cid: str, name: str) -> list[StreamEvent]:
    return [StreamEvent(type="tool_call", tool_call=ToolCall(id=cid, name=name, arguments={}))]


async def make_conv() -> dict:
    conv = await db.create_conversation("i", "fake-model", agent_mode=True)
    await db.add_message(conv["id"], "user", "go")
    return await db.get_conversation(conv["id"])


async def test_a_trusted_tool_runs_without_asking():
    tool = GatedTool("trusted")
    use(tool)
    await db.set_setting("auto_approve_tools", '["trusted"]')
    conv = await make_conv()
    provider = FakeProvider([call("c1", "trusted")])
    events = [ev async for ev in stream_agent_chat(conv, provider)]

    assert tool.runs == 1, "a trusted tool should run straight through"
    assert not any(ev.get("type") == "approval_required" for ev in events)
    results = [ev for ev in events if ev.get("type") == "tool_result"]
    assert results and results[0].get("auto_approved"), \
        "a rule-approved call ran unreviewed - it must wear the marker"
    await db.set_setting("auto_approve_tools", "[]")
    print("per-tool rule: trusted tool ran unreviewed, marked OK")


async def test_an_untrusted_tool_still_asks():
    tool = GatedTool("other")
    use(tool)
    await db.set_setting("auto_approve_tools", '["trusted"]')  # not this one
    conv = await make_conv()
    provider = FakeProvider([call("c1", "other")])
    prompts = 0
    async for ev in stream_agent_chat(conv, provider):
        if ev.get("type") == "approval_required":
            prompts += 1
            agent.resolve_approval(ev["approval_id"], True)
    assert prompts == 1 and tool.runs == 1
    await db.set_setting("auto_approve_tools", "[]")
    print("per-tool rule: a tool NOT on the list still parks on approval OK")


async def test_pause_parks_at_the_step_boundary_and_resume_continues():
    use(FreeTool())
    conv = await make_conv()
    provider = FakeProvider([call("c1", "free"), [StreamEvent(type="text_delta", text="finished")]])

    events = []

    async def drive():
        async for ev in stream_agent_chat(conv, provider):
            events.append(ev)
            if ev.get("type") == "tool_result":
                agent.request_pause(conv["id"])  # the user clicks Pause here

    task = asyncio.create_task(drive())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if any(ev.get("type") == "paused" for ev in events):
            break
    assert any(ev.get("type") == "paused" for ev in events), \
        f"the run never parked: {[e.get('type') for e in events]}"
    assert not task.done(), "a parked run must wait, not end"

    assert agent.request_resume(conv["id"]) is True
    await asyncio.wait_for(task, timeout=5)
    types = [ev.get("type") for ev in events]
    assert "resumed" in types and types.index("resumed") > types.index("paused"), types
    assert "finished" in "".join(ev.get("text") or "" for ev in events), \
        "the run should have continued to its answer after resuming"
    print("pause: parked at the boundary, resumed, finished OK")


async def test_resuming_nothing_is_a_refusal():
    assert agent.request_resume("no-such-conv") is False
    print("resume with nothing parked: refused, not a silent no-op OK")


async def test_stop_while_paused_tears_down_cleanly():
    use(FreeTool())
    conv = await make_conv()
    provider = FakeProvider([call("c1", "free"), [StreamEvent(type="text_delta", text="later")]])
    agent.request_pause(conv["id"])  # pause before the run even starts

    events = []

    async def drive():
        async for ev in stream_agent_chat(conv, provider):
            events.append(ev)

    task = asyncio.create_task(drive())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if any(ev.get("type") == "paused" for ev in events):
            break
    assert any(ev.get("type") == "paused" for ev in events), "the run never parked"

    task.cancel()  # Stop, a closed tab, a dropped connection
    try:
        await task
    except asyncio.CancelledError:
        pass
    await drain_detached_writes()

    assert conv["id"] not in agent._resume_waiters, "the parked waiter leaked"
    assert conv["id"] not in agent._pause_flags, "the pause flag leaked"
    assert not any(ev.get("type") == "resumed" for ev in events)
    print("Stop while paused: torn down, no waiter or flag leaked OK")


async def main():
    try:
        await test_a_trusted_tool_runs_without_asking()
        await test_an_untrusted_tool_still_asks()
        await test_pause_parks_at_the_step_boundary_and_resume_continues()
        await test_resuming_nothing_is_a_refusal()
        await test_stop_while_paused_tears_down_cleanly()
    finally:
        await db.close_db()
    print("\nALL RUN CONTROL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
