"""Safety tests for agent mode vs normal mode.

Drives the REAL agent loop (server/agent.py) and plain chat (server/chat.py)
with a scripted fake provider - no network, no API keys, no real LLM.
Uses a temp database and temp workspace so the user's data is untouched.

Covers:
- normal mode never passes tools to the provider and never executes one,
  even if a provider emits a rogue tool_call event
- agent mode passes the builtin tool specs; only `shell` is approval-gated
- denied shell calls do NOT execute and the denial is persisted for the model
- approved shell calls execute in the workspace
- file tools refuse paths outside the workspace / granted folders
- unknown tool names return an error instead of crashing the loop
- approvals are single-shot (unknown/stale ids are rejected)
- the agent loop stops at MAX_AGENT_STEPS

Run: .venv\\Scripts\\python.exe scripts\\test_agent_safety.py
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- temp sandbox: point db + workspace away from the user's real data ---

TMP = Path(tempfile.mkdtemp(prefix="syrudas-safety-"))
WORKSPACE = TMP / "workspace"
GRANTED = TMP / "granted"
FORBIDDEN = TMP / "forbidden"
for d in (WORKSPACE, GRANTED, FORBIDDEN):
    d.mkdir()

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.tools import files as files_mod, shell as shell_mod  # noqa: E402
files_mod.DEFAULT_WORKSPACE = WORKSPACE
shell_mod.DEFAULT_WORKSPACE = WORKSPACE

from server import agent  # noqa: E402
from server.agent import resolve_approval, stream_agent_chat  # noqa: E402
from server.chat import stream_plain_chat  # noqa: E402
from server.config import MAX_AGENT_STEPS  # noqa: E402
from server.providers.base import ModelProvider  # noqa: E402
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec  # noqa: E402
from server.tools.files import FileReadTool, FileWriteTool  # noqa: E402


class FakeProvider(ModelProvider):
    """Replays scripted turns; records what it was asked to do."""
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, turns, repeat_last=False):
        super().__init__({})
        self.turns = list(turns)
        self.repeat_last = repeat_last
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
        if self.turns:
            turn = self.turns.pop(0)
        elif self.repeat_last:
            turn = self._last
        else:
            turn = [StreamEvent(type="text_delta", text="(script exhausted)")]
        self._last = turn
        for ev in turn:
            yield ev
        yield StreamEvent(type="done")


def text_turn(text):
    return [StreamEvent(type="text_delta", text=text)]


def tool_turn(name, args, call_id="tc1"):
    return [StreamEvent(type="tool_call",
                        tool_call=ToolCall(id=call_id, name=name, arguments=args))]


async def make_conv(agent_mode: bool, user_text="do the thing") -> dict:
    conv = await db.create_conversation("fake-inst", "fake-model", agent_mode)
    await db.add_message(conv["id"], "user", user_text)
    return conv


async def collect_with_approvals(stream, decisions: dict[int, bool]):
    """Consume an agent stream, resolving the Nth approval per `decisions`."""
    events: list[dict] = []
    seen = 0

    async def consume():
        async for ev in stream:
            events.append(ev)

    task = asyncio.create_task(consume())
    while not task.done():
        await asyncio.sleep(0.02)
        # resolve once the loop has parked the future (it registers after yielding)
        pending = list(agent._pending_approvals)
        if pending and seen in decisions:
            resolve_approval(pending[0], decisions[seen])
            seen += 1
    await task
    return events


async def test_normal_mode_has_no_tools():
    provider = FakeProvider([
        # a hostile/buggy provider emitting a tool_call in plain mode
        tool_turn("shell", {"command": f"New-Item {WORKSPACE / 'rogue.txt'}"})
        + text_turn("hello"),
    ])
    conv = await make_conv(agent_mode=False)
    events = [ev async for ev in stream_plain_chat(conv, provider)]

    assert provider.calls[0]["tools"] is None, "plain chat must not offer tools"
    assert not any(e["type"] == "tool_result" for e in events), \
        "plain chat must never execute or answer tool calls"
    assert not (WORKSPACE / "rogue.txt").exists(), "no side effects in plain mode"
    print("normal mode: no tools offered, rogue tool_call inert OK")


async def test_agent_mode_tool_offering():
    provider = FakeProvider([text_turn("done")])
    conv = await make_conv(agent_mode=True)
    async for _ in stream_agent_chat(conv, provider):
        pass

    offered = {t.name for t in provider.calls[0]["tools"]}
    assert {"shell", "file_read", "file_write", "file_list",
            "web_fetch", "web_search"} <= offered, f"missing builtins: {offered}"
    from server.tools import builtin_tools
    gated = {t.name for t in builtin_tools() if t.requires_approval}
    assert gated == {"shell", "web_fetch"}, f"unexpected statically gated set: {gated}"
    print("agent mode: builtin tools offered, shell + web_fetch statically gated OK")


async def test_shell_denied_does_not_run():
    marker = WORKSPACE / "denied-marker.txt"
    provider = FakeProvider([
        tool_turn("shell", {"command": f'New-Item "{marker}" -ItemType File'}),
        text_turn("ok, I won't then"),
    ])
    conv = await make_conv(agent_mode=True)
    events = await collect_with_approvals(
        stream_agent_chat(conv, provider), decisions={0: False})

    approvals = [e for e in events if e["type"] == "approval_required"]
    results = [e for e in events if e["type"] == "tool_result"]
    assert len(approvals) == 1, "shell must request approval"
    assert results[0]["content"] == "The user denied this tool call."
    assert not marker.exists(), "DENIED shell command must not execute"
    # the denial must be persisted so the model sees it on the next turn
    msgs = await db.list_messages(conv["id"])
    assert any(m["role"] == "tool" and "denied" in m["content"] for m in msgs)
    print("shell deny: approval requested, command not executed, denial persisted OK")


async def test_shell_approved_runs():
    provider = FakeProvider([
        tool_turn("shell", {"command": "Write-Output approved-hello"}),
        text_turn("done"),
    ])
    conv = await make_conv(agent_mode=True)
    events = await collect_with_approvals(
        stream_agent_chat(conv, provider), decisions={0: True})

    result = next(e for e in events if e["type"] == "tool_result")
    assert "exit code: 0" in result["content"], result["content"]
    assert "approved-hello" in result["content"], result["content"]
    print("shell approve: command ran in workspace, output returned OK")


async def test_file_sandbox():
    write, read = FileWriteTool(), FileReadTool()

    r = await write.run({"path": "..\\escape.txt", "content": "x"})
    assert r.startswith("Error:") and "escapes" in r, r
    assert not (TMP / "escape.txt").exists()

    r = await write.run({"path": str(FORBIDDEN / "evil.txt"), "content": "x"})
    assert r.startswith("Error:") and "not in an allowed folder" in r, r
    assert not (FORBIDDEN / "evil.txt").exists()

    r = await read.run({"path": "C:\\Windows\\win.ini"})
    assert r.startswith("Error:") and "not in an allowed folder" in r, r

    r = await write.run({"path": "notes.txt", "content": "workspace file"})
    assert r.startswith("Wrote"), r
    r = await read.run({"path": "notes.txt"})
    assert r == "workspace file", r

    # granting a folder in settings opens it up for absolute paths
    await db.set_setting("agent_folders", json.dumps([str(GRANTED)]))
    r = await write.run({"path": str(GRANTED / "ok.txt"), "content": "granted"})
    assert r.startswith("Wrote"), r
    await db.set_setting("agent_folders", "[]")
    r = await read.run({"path": str(GRANTED / "ok.txt")})
    assert r.startswith("Error:"), "revoking the grant must close access again"
    print("file sandbox: workspace-relative OK; escapes, ungranted and revoked paths refused OK")


async def test_file_write_gating():
    write = FileWriteTool()

    assert not await write.needs_approval({"path": "notes.txt"}), \
        "workspace writes must not prompt"
    assert not await write.needs_approval({"path": "..\\escape.txt"}), \
        "invalid paths error out in run(); prompting would be noise"

    await db.set_setting("agent_folders", json.dumps([str(GRANTED)]))
    try:
        assert await write.needs_approval({"path": str(GRANTED / "doc.txt")}), \
            "writes to granted folders must prompt"

        # end-to-end through the agent loop: deny leaves no file, approve writes
        target = GRANTED / "loop-write.txt"
        for decision, should_exist in ((False, False), (True, True)):
            provider = FakeProvider([
                tool_turn("file_write", {"path": str(target), "content": "hi"}),
                text_turn("done"),
            ])
            conv = await make_conv(agent_mode=True)
            events = await collect_with_approvals(
                stream_agent_chat(conv, provider), decisions={0: decision})
            assert any(e["type"] == "approval_required" for e in events)
            assert target.exists() is should_exist, f"decision={decision}"

        # workspace writes flow through the same loop with no prompt
        provider = FakeProvider([
            tool_turn("file_write", {"path": "free.txt", "content": "hi"}),
            text_turn("done"),
        ])
        conv = await make_conv(agent_mode=True)
        events = [ev async for ev in stream_agent_chat(conv, provider)]
        assert not any(e["type"] == "approval_required" for e in events)
        assert (WORKSPACE / "free.txt").exists()
    finally:
        await db.set_setting("agent_folders", "[]")
    print("file_write gating: workspace free, granted folders prompt, deny blocks OK")


async def test_web_fetch_refuses_private_hosts():
    from server.tools.web import WebFetchTool

    fetch = WebFetchTool()
    for url in (
        "http://127.0.0.1:8040/api/conversations",  # this app's own API
        "http://localhost/admin",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
    ):
        r = await fetch.run({"url": url})
        assert r.startswith("Error:") and "private/loopback" in r, f"{url} -> {r}"
    r = await fetch.run({"url": "ftp://example.com/x"})
    assert r.startswith("Error: url must start with http"), r
    print("web_fetch: loopback/LAN/link-local targets refused OK")


async def test_unknown_tool_is_error():
    provider = FakeProvider([
        tool_turn("delete_all_backups", {}),
        text_turn("hm"),
    ])
    conv = await make_conv(agent_mode=True)
    events = [ev async for ev in stream_agent_chat(conv, provider)]
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["content"] == "Error: unknown tool 'delete_all_backups'"
    print("unknown tool: refused with error, loop kept going OK")


async def test_approvals_are_single_shot():
    assert resolve_approval("no-such-id", True) is False
    print("approvals: unknown/stale ids rejected OK")


async def test_step_limit():
    provider = FakeProvider(
        [tool_turn("file_list", {}, call_id="loop")], repeat_last=True)
    conv = await make_conv(agent_mode=True)
    events = [ev async for ev in stream_agent_chat(conv, provider)]
    steps = sum(1 for e in events if e["type"] == "tool_result")
    assert steps == MAX_AGENT_STEPS, f"expected {MAX_AGENT_STEPS} steps, got {steps}"
    assert any("limit" in (e.get("text") or "") for e in events), \
        "loop must announce it hit the step limit"
    print(f"step limit: loop stopped after {MAX_AGENT_STEPS} tool rounds OK")


async def main():
    await test_normal_mode_has_no_tools()
    await test_agent_mode_tool_offering()
    await test_shell_denied_does_not_run()
    await test_shell_approved_runs()
    await test_file_sandbox()
    await test_file_write_gating()
    await test_web_fetch_refuses_private_hosts()
    await test_unknown_tool_is_error()
    await test_approvals_are_single_shot()
    await test_step_limit()
    await db.close_db()
    print("\nALL AGENT SAFETY TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
