"""The remaining Stage 1 guards: bad arguments, repetition, empty steps, dead MCP.

Each of these let one bad turn consume the whole run:

- arguments went to a tool unchecked, so a model's malformed JSON arrived as
  {"_raw": "..."} and the tool did whatever it does with nothing;
- the same failing call could repeat until the fifteen-step ceiling, with an
  approval prompt each time;
- a step that produced nothing at all ended the run, even though a model still
  loading produces exactly that;
- a dead MCP server was reconnected on every message, and the connect timeout
  is 90 seconds.

Run: .venv\\Scripts\\python.exe scripts\\test_agent_guards.py
"""
import asyncio
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-guards-"))
WORKSPACE = TMP / "workspace"
WORKSPACE.mkdir()

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.tools import files as files_mod  # noqa: E402
files_mod.DEFAULT_WORKSPACE = WORKSPACE

from server import agent, mcp_client  # noqa: E402
from server.agent import (  # noqa: E402
    MAX_EMPTY_RETRIES, MAX_IDENTICAL_CALLS, _schema_complaint, stream_agent_chat)
from server.providers.base import ModelProvider  # noqa: E402
from server.schemas import (  # noqa: E402
    GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec)
from server.tools import Tool  # noqa: E402


class CountingTool(Tool):
    name = "counter"
    description = "counts how many times it actually ran"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    def __init__(self):
        self.runs = 0

    async def run(self, args: dict[str, Any]) -> str:
        self.runs += 1
        return "Error: still broken"


class FakeProvider(ModelProvider):
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, turns):
        super().__init__({})
        self.turns = list(turns)
        self.calls = 0

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model")]

    async def chat(self, model, messages, tools=None, params=None) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        turn = self.turns.pop(0) if self.turns else [StreamEvent(type="text_delta", text="done")]
        for ev in turn:
            yield ev
        yield StreamEvent(type="done")


def use(*tools: Tool) -> None:
    async def collect():
        return list(tools)
    agent.collect_tools = collect


def call(cid: str, args: dict) -> list[StreamEvent]:
    return [StreamEvent(type="tool_call",
                        tool_call=ToolCall(id=cid, name="counter", arguments=args))]


async def make_conv() -> dict:
    conv = await db.create_conversation("i", "fake-model", agent_mode=True)
    await db.add_message(conv["id"], "user", "go")
    return await db.get_conversation(conv["id"])


# --- 1.6 argument validation ---

def test_malformed_json_is_named_not_executed():
    tool = CountingTool()
    msg = _schema_complaint(tool, {"_raw": "{value: broken"})
    assert msg and "not valid JSON" in msg, msg
    assert "value" in msg, "the complaint should show the expected schema"
    assert tool.runs == 0
    print("malformed arguments: refused with the schema, tool never ran OK")


def test_missing_and_unexpected_arguments_are_named():
    tool = CountingTool()
    missing = _schema_complaint(tool, {})
    assert missing and "missing required" in missing and "value" in missing, missing
    unknown = _schema_complaint(tool, {"value": "x", "extra": 1})
    assert unknown and "unexpected" in unknown and "extra" in unknown, unknown
    assert _schema_complaint(tool, {"value": "x"}) is None, "a valid call must pass"
    print("missing / unexpected arguments: named; a valid call passes OK")


# --- 1.6 repetition ---

async def test_the_same_failing_call_stops_repeating():
    tool = CountingTool()
    use(tool)
    conv = await make_conv()
    # the model stubbornly repeats one identical call every step
    provider = FakeProvider([call(f"c{i}", {"value": "same"}) for i in range(8)])
    async for _ in stream_agent_chat(conv, provider):
        pass

    assert tool.runs == MAX_IDENTICAL_CALLS, \
        f"expected {MAX_IDENTICAL_CALLS} real executions, got {tool.runs}"
    print(f"identical failing call: executed {tool.runs}x then refused OK")


async def test_different_arguments_are_not_throttled():
    tool = CountingTool()
    use(tool)
    conv = await make_conv()
    provider = FakeProvider([call(f"c{i}", {"value": f"different-{i}"}) for i in range(5)])
    async for _ in stream_agent_chat(conv, provider):
        pass
    assert tool.runs == 5, f"varied calls must all run, got {tool.runs}"
    print("varied arguments: never throttled OK")


# --- 1.11 empty-step retry ---

async def test_an_empty_step_is_retried_then_given_up_on():
    use(CountingTool())
    conv = await make_conv()
    # every turn produces nothing at all, as a model still loading does
    provider = FakeProvider([[] for _ in range(10)])
    agent.EMPTY_RETRY_BACKOFF_S = (0.0, 0.0)     # keep the test quick
    events = [ev async for ev in stream_agent_chat(conv, provider)]

    assert provider.calls == MAX_EMPTY_RETRIES + 1, \
        f"expected {MAX_EMPTY_RETRIES} retries, provider called {provider.calls}x"
    assert any("empty response" in (e.get("text") or "") for e in events), \
        "after the retries it should say why it stopped"
    print(f"empty step: retried {MAX_EMPTY_RETRIES}x, then reported honestly OK")


async def test_a_productive_step_clears_the_retry_budget():
    use(CountingTool())
    conv = await make_conv()
    agent.EMPTY_RETRY_BACKOFF_S = (0.0, 0.0)
    provider = FakeProvider([
        [],                                   # empty -> retry
        [StreamEvent(type="text_delta", text="recovered")],
    ])
    events = [ev async for ev in stream_agent_chat(conv, provider)]
    assert any("recovered" in (e.get("text") or "") for e in events), events
    assert not any("empty response" in (e.get("text") or "") for e in events)
    print("a retry that succeeds: run continues, no stop note OK")


# --- 1.12 MCP cooldown ---

async def test_a_dead_mcp_server_is_not_retried_every_message():
    attempts = {"n": 0}

    class _DeadConn:
        def __init__(self, server):
            attempts["n"] += 1
            self.server = server
            self.session = None
            self.error = "refused"
            self.config_key = mcp_client._config_key(server)
            self._ready = asyncio.Event()
            self._ready.set()

        @property
        def alive(self):
            return False

        async def wait_ready(self):
            return None

        async def close(self):
            return None

    mcp_client._Conn = _DeadConn
    mcp_client._conns.clear()
    mcp_client._cooldown.clear()
    server = {"id": "s1", "name": "dead", "command": "nope", "args": [], "env": {}}

    assert await mcp_client._get_conn(server) is None
    first = attempts["n"]
    for _ in range(5):
        assert await mcp_client._get_conn(server) is None
    assert attempts["n"] == first, \
        f"a failed server was reconnected {attempts['n'] - first} more times"

    # the cooldown expires rather than blocking the server forever
    mcp_client._cooldown["s1"] = time.monotonic() - 1
    await mcp_client._get_conn(server)
    assert attempts["n"] > first, "after the cooldown it should try again"
    print(f"dead MCP server: {first} attempt, then held off, then retried OK")


async def main():
    try:
        test_malformed_json_is_named_not_executed()
        test_missing_and_unexpected_arguments_are_named()
        await test_the_same_failing_call_stops_repeating()
        await test_different_arguments_are_not_throttled()
        await test_an_empty_step_is_retried_then_given_up_on()
        await test_a_productive_step_clears_the_retry_budget()
        await test_a_dead_mcp_server_is_not_retried_every_message()
    finally:
        await db.close_db()
    print("\nALL AGENT GUARD TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
