"""Interruption tests: a stopped agent run must leave a replayable transcript.

Drives the REAL agent loop (server/agent.py) with a scripted fake provider -
no network, no API keys, no real LLM. Uses a temp database and temp workspace.

An assistant tool_call with no answering tool message is rejected outright by
both OpenAI and Anthropic, and the rows are on disk before any tool runs - so
a run torn down mid-tool used to brick the conversation for good.

Covers:
- cancelling mid-tool-call answers the tool_call and records why the run ended
- cancelling while parked on an approval does the same, and leaks no approval id
- an approval id is claimable the instant the client sees the event (no race)
- a needs_approval that raises becomes a tool result, not an unanswered call
- a tool that succeeded keeps its result when the run is torn down at the yield
- teardown at a yield (GeneratorExit, not cancellation) records a stop reason
- a detached write that fails is logged rather than silently dropped
- cancelling between two calls of one step marks only the call in flight
- the repair pass and a still-in-flight write never double-answer a tool_call
- build_history repairs conversations damaged before any of this existed
- the repaired history is a valid provider request, and stays valid on replay

Run: .venv\\Scripts\\python.exe scripts\\test_agent_interrupt.py
"""
import asyncio
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-interrupt-"))
WORKSPACE = TMP / "workspace"
WORKSPACE.mkdir()

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.tools import files as files_mod  # noqa: E402
files_mod.DEFAULT_WORKSPACE = WORKSPACE

from server import agent  # noqa: E402
from server.agent import drain_detached_writes, stream_agent_chat  # noqa: E402
from server.chat import INTERRUPTED_TOOL_RESULT, build_history  # noqa: E402
from server.providers.base import ModelProvider  # noqa: E402
from server.providers.openai_compat import _wire_messages  # noqa: E402
from server.schemas import (  # noqa: E402
    GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec)
from server.tools import Tool  # noqa: E402


class FakeProvider(ModelProvider):
    """Replays scripted turns."""
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, turns):
        super().__init__({})
        self.turns = list(turns)

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model")]

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        params: Optional[GenParams] = None,
    ) -> AsyncIterator[StreamEvent]:
        turn = self.turns.pop(0) if self.turns else [
            StreamEvent(type="text_delta", text="(script exhausted)")]
        for ev in turn:
            yield ev
        yield StreamEvent(type="done")


class BlockingTool(Tool):
    """Parks forever so a run can be cancelled while it is mid-call."""
    name = "blocking"
    description = "blocks"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self):
        self.started = asyncio.Event()

    async def run(self, args: dict[str, Any]) -> str:
        self.started.set()
        await asyncio.sleep(3600)
        return "never reached"


class InstantTool(Tool):
    """Succeeds immediately, so teardown can be aimed at the yield after it."""
    name = "instant"
    description = "returns immediately"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    RESULT = "the real tool output"

    async def run(self, args: dict[str, Any]) -> str:
        return self.RESULT


class GatedTool(Tool):
    name = "gated"
    description = "always needs approval"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    requires_approval = True

    async def run(self, args: dict[str, Any]) -> str:
        return "gated tool ran"


class ExplodingGateTool(Tool):
    """needs_approval raises - used to run outside the loop's exception guard."""
    name = "exploding_gate"
    description = "its gating check raises"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def needs_approval(self, args: dict[str, Any]) -> bool:
        raise RuntimeError("gate blew up")

    async def run(self, args: dict[str, Any]) -> str:
        return "should not run"


def use_tools(*tools: Tool) -> None:
    async def collect() -> list[Tool]:
        return list(tools)
    agent.collect_tools = collect


def tool_turn(name, call_id="tc1", args=None):
    return [StreamEvent(type="tool_call",
                        tool_call=ToolCall(id=call_id, name=name, arguments=args or {}))]


def multi_tool_turn(*calls):
    """One model turn emitting several tool_calls, as a parallel-call model does."""
    return [StreamEvent(type="tool_call",
                        tool_call=ToolCall(id=call_id, name=name, arguments={}))
            for name, call_id in calls]


async def make_conv(user_text="do the thing") -> dict:
    conv = await db.create_conversation("fake-inst", "fake-model", agent_mode=True)
    await db.add_message(conv["id"], "user", user_text)
    return await db.get_conversation(conv["id"])


async def unanswered_calls(conv_id: str) -> list[str]:
    """tool_call ids with no answering tool row, straight from the database."""
    rows = await db.list_messages(conv_id)
    answered = {r["tool_call_id"] for r in rows if r["role"] == "tool"}
    return [tc["id"] for r in rows for tc in (r["tool_calls"] or [])
            if tc["id"] not in answered]


def wire_is_valid(history: list[Message]) -> bool:
    """Every tool_call answered later in the list, as the APIs require."""
    for i, m in enumerate(history):
        for tc in (m.tool_calls or []):
            if not any(n.role == "tool" and n.tool_call_id == tc.id
                       for n in history[i + 1:]):
                return False
    return True


async def cancel_during(conv, provider, ready, drain=True) -> None:
    """Consume the agent stream until `ready` fires, then cancel it.

    drain=False leaves the handed-off writes in flight, so a caller can look
    at the conversation the way a fast follow-up request would see it.
    """
    async def consume():
        async for _ in stream_agent_chat(conv, provider):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(ready(), timeout=5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    if drain:
        await drain_detached_writes()


async def park_then_close(conv, provider) -> Optional[dict]:
    """Abandon the run while it is suspended at a tool_result yield.

    That is where a dropped connection actually lands - the generator is
    parked at the yield while the consumer writes to the socket - and it
    arrives as GeneratorExit rather than as a cancellation.
    """
    stream = stream_agent_chat(conv, provider)
    seen = None
    async for ev in stream:
        if ev["type"] == "tool_result":
            seen = ev
            break
    await stream.aclose()
    await drain_detached_writes()
    return seen


# --- tests ---

async def test_cancel_mid_tool_call():
    blocking = BlockingTool()
    use_tools(blocking)
    conv = await make_conv()
    provider = FakeProvider([tool_turn("blocking", "call_mid")])

    await cancel_during(conv, provider, lambda: blocking.started.wait())

    orphans = await unanswered_calls(conv["id"])
    assert not orphans, f"cancelled run left unanswered tool_calls: {orphans}"
    rows = await db.list_messages(conv["id"])
    answers = [r for r in rows if r["role"] == "tool"]
    assert answers and answers[0]["content"] == INTERRUPTED_TOOL_RESULT, \
        "the unrun tool call must be answered with the interrupted marker"
    assert any("cancelled" in r["content"] for r in rows if r["role"] == "assistant"), \
        "the transcript must record that the run was cancelled"
    print("cancel mid-tool-call: tool_call answered + reason recorded OK")


async def test_cancel_mid_tool_call_replays():
    """The real point of the fix: the next turn must be a legal request."""
    conv = await db.list_conversations()
    conv = await db.get_conversation(conv[0]["id"])
    await db.add_message(conv["id"], "user", "never mind, do something else")

    history = await build_history(conv)
    assert wire_is_valid(history), "repaired history still has an unanswered tool_call"
    wire = _wire_messages(history)
    for i, msg in enumerate(wire):
        for tc in msg.get("tool_calls") or []:
            assert any(w.get("tool_call_id") == tc["id"] for w in wire[i + 1:]), \
                f"tool_call {tc['id']} goes on the wire unanswered"
    print("cancel mid-tool-call: next request is wire-valid OK")


async def test_cancel_while_awaiting_approval():
    use_tools(GatedTool())
    conv = await make_conv()
    provider = FakeProvider([tool_turn("gated", "call_gated")])

    async def parked():
        while not agent._pending_approvals:
            await asyncio.sleep(0.01)

    await cancel_during(conv, provider, parked)

    orphans = await unanswered_calls(conv["id"])
    assert not orphans, f"cancelled approval left unanswered tool_calls: {orphans}"
    assert not agent._pending_approvals, \
        f"cancelled run leaked approval ids: {list(agent._pending_approvals)}"
    print("cancel while awaiting approval: answered + no leaked approval id OK")


async def test_approval_id_is_claimable_when_announced():
    """The event and the POST answering it race; the id must already exist."""
    use_tools(GatedTool())
    conv = await make_conv()
    provider = FakeProvider([tool_turn("gated", "call_race")])

    events = []
    stream = stream_agent_chat(conv, provider)
    async for ev in stream:
        events.append(ev)
        if ev["type"] == "approval_required":
            # exactly what the client can do the instant it reads the line
            assert agent.resolve_approval(ev["approval_id"], True), \
                "approval id was announced before it could be resolved"
            break
    await stream.aclose()
    await drain_detached_writes()
    assert any(e["type"] == "approval_required" for e in events)
    print("approval race: id resolvable the moment it is announced OK")


async def test_raising_gate_becomes_a_tool_result():
    use_tools(ExplodingGateTool())
    conv = await make_conv()
    provider = FakeProvider([tool_turn("exploding_gate", "call_boom")])

    logging.disable(logging.ERROR)  # the gate is meant to raise; don't print it
    try:
        events = [ev async for ev in stream_agent_chat(conv, provider)]
    finally:
        logging.disable(logging.NOTSET)

    orphans = await unanswered_calls(conv["id"])
    assert not orphans, f"a raising gate left unanswered tool_calls: {orphans}"
    results = [e for e in events if e["type"] == "tool_result"]
    assert results and "gate blew up" in results[0]["content"], \
        f"expected the gate failure as a tool result, got {results}"
    print("raising needs_approval: became a tool result, loop survived OK")


async def test_cancel_between_tool_calls_in_one_step():
    """Only the call actually in flight is marked; earlier ones keep their output."""
    blocking = BlockingTool()
    use_tools(InstantTool(), blocking)
    conv = await make_conv("two calls in one step")
    provider = FakeProvider([multi_tool_turn(("instant", "multi_1"),
                                             ("blocking", "multi_2"))])

    await cancel_during(conv, provider, lambda: blocking.started.wait())

    assert not await unanswered_calls(conv["id"])
    results = {r["tool_call_id"]: r["content"]
               for r in await db.list_messages(conv["id"]) if r["role"] == "tool"}
    assert results.get("multi_1") == InstantTool.RESULT, \
        f"a call that finished earlier in the step was clobbered: {results}"
    assert results.get("multi_2") == INTERRUPTED_TOOL_RESULT, \
        f"the in-flight call was not answered: {results}"
    print("cancel between calls in one step: only the in-flight one marked OK")


async def test_repair_does_not_duplicate_a_detached_write():
    """A follow-up request can arrive before the handed-off write lands.

    Whichever gets there first, the turn must end up with exactly one answer:
    the repair pass only synthesizes in memory, so it can never race a row
    onto disk alongside the real one.
    """
    blocking = BlockingTool()
    use_tools(blocking)
    conv = await make_conv("race the repair pass")
    provider = FakeProvider([tool_turn("blocking", "race_1")])

    await cancel_during(conv, provider, lambda: blocking.started.wait(), drain=False)

    early = await build_history(await db.get_conversation(conv["id"]))
    assert wire_is_valid(early), "history was invalid in the window before the write landed"
    n_early = sum(1 for m in early if m.tool_call_id == "race_1")

    await drain_detached_writes()
    late = await build_history(await db.get_conversation(conv["id"]))
    n_late = sum(1 for m in late if m.tool_call_id == "race_1")
    on_disk = sum(1 for r in await db.list_messages(conv["id"])
                  if r["tool_call_id"] == "race_1")

    assert n_early == n_late == on_disk == 1, \
        f"tool_call answered more than once (early={n_early} late={n_late} disk={on_disk})"
    print("repair vs detached write: exactly one answer either way OK")


async def test_build_history_repairs_old_damage():
    """Conversations corrupted before the loop learned to close its own gaps."""
    conv = await make_conv("clean up the temp files")
    await db.add_message(
        conv["id"], "assistant", "Running that now.",
        tool_calls=[ToolCall(id="legacy_1", name="shell",
                             arguments={"command": "Remove-Item *.tmp"}).model_dump()])
    await db.add_message(conv["id"], "user", "actually never mind")
    conv = await db.get_conversation(conv["id"])

    history = await build_history(conv)
    assert wire_is_valid(history), "build_history left a pre-existing orphan unanswered"
    repaired = [m for m in history if m.role == "tool" and m.tool_call_id == "legacy_1"]
    # `in`, not `==`: every tool message is fenced as untrusted data on its way
    # to the model, synthesized ones included - one rule with no exception an
    # attacker could aim for
    assert len(repaired) == 1 and INTERRUPTED_TOOL_RESULT in repaired[0].content
    # position matters: the Anthropic adapter only merges results into the
    # tool_use block immediately before them
    idx = history.index(repaired[0])
    assert history[idx - 1].tool_calls, \
        "the synthesized result must sit directly after its assistant turn"
    print("build_history: repairs damage already on disk, in place OK")


async def test_repair_leaves_healthy_history_alone():
    use_tools(GatedTool())
    conv = await make_conv("a normal completed run")
    await db.add_message(
        conv["id"], "assistant", "",
        tool_calls=[ToolCall(id="ok_1", name="gated", arguments={}).model_dump()])
    await db.add_message(conv["id"], "tool", "gated tool ran", tool_call_id="ok_1")
    conv = await db.get_conversation(conv["id"])

    history = await build_history(conv)
    assert not [m for m in history if m.content == INTERRUPTED_TOOL_RESULT], \
        "repair pass invented a result for an already-answered tool_call"
    assert sum(1 for m in history if m.role == "tool") == 1
    print("build_history: healthy history passes through untouched OK")


async def test_completed_result_survives_disconnect():
    """A tool that succeeded must not be overwritten by the teardown marker."""
    use_tools(InstantTool())
    conv = await make_conv("disconnect at the yield")
    ev = await park_then_close(conv, FakeProvider([tool_turn("instant", "keep_1")]))

    assert ev and ev["content"] == InstantTool.RESULT
    stored = [r["content"] for r in await db.list_messages(conv["id"])
              if r["tool_call_id"] == "keep_1"]
    assert stored == [InstantTool.RESULT], \
        f"a completed tool result was lost on teardown: {stored}"
    print("disconnect at tool_result yield: real result kept OK")


async def test_teardown_at_a_yield_records_reason():
    """GeneratorExit is a different teardown than cancellation; same duty."""
    use_tools(InstantTool())
    conv = await make_conv("generator torn down at a yield")
    await park_then_close(conv, FakeProvider([tool_turn("instant", "note_1")]))

    rows = await db.list_messages(conv["id"])
    assert not await unanswered_calls(conv["id"])
    assert any("cancelled" in r["content"] for r in rows if r["role"] == "assistant"), \
        "teardown at a yield recorded no stop reason"
    print("teardown at a yield: stop reason recorded OK")


async def test_failed_detached_write_is_logged():
    async def boom():
        raise RuntimeError("disk full")

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("server.agent")
    handler, propagate = Capture(), logger.propagate
    logger.addHandler(handler)
    logger.propagate = False  # keep the deliberate traceback out of the output
    try:
        agent._persist_detached(boom())
        await drain_detached_writes()
    finally:
        logger.removeHandler(handler)
        logger.propagate = propagate

    assert any(r.levelno >= logging.ERROR for r in records), \
        "a failed detached write vanished without a log line"
    print("failed detached write: logged instead of swallowed OK")


async def main():
    try:
        await test_cancel_mid_tool_call()
        await test_cancel_mid_tool_call_replays()
        await test_cancel_while_awaiting_approval()
        await test_approval_id_is_claimable_when_announced()
        await test_raising_gate_becomes_a_tool_result()
        await test_completed_result_survives_disconnect()
        await test_teardown_at_a_yield_records_reason()
        await test_failed_detached_write_is_logged()
        await test_cancel_between_tool_calls_in_one_step()
        await test_repair_does_not_duplicate_a_detached_write()
        await test_build_history_repairs_old_damage()
        await test_repair_leaves_healthy_history_alone()
        print("\nALL AGENT INTERRUPT TESTS PASSED")

    finally:
        # aiosqlite's connection thread is not a daemon, so a failing
        # assertion that skipped the close left the interpreter hanging at
        # exit: the suite never reported its failure, it just stopped, and
        # in CI that is a job running to the time limit instead of a red X.
        await db.close_db()

if __name__ == "__main__":
    asyncio.run(main())
