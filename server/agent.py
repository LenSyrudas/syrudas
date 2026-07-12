"""Agent mode: model <-> tools loop with a per-call approval gate for gated tools.

The chat HTTP stream is one-way, so approvals arrive out-of-band via
POST /api/approvals/{id}; the loop parks on an asyncio.Future until then.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator, Optional

from . import db
from .chat import build_history, persist_if_current
from .config import DEFAULT_WORKSPACE, MAX_AGENT_STEPS
from .providers.base import ModelProvider
from .schemas import GenParams, Message, ToolCall
from .tools import Tool, builtin_tools

log = logging.getLogger(__name__)

APPROVAL_TIMEOUT_S = 15 * 60
_pending_approvals: dict[str, asyncio.Future] = {}

AGENT_SYSTEM_PROMPT = f"""You are Syrudas, an autonomous assistant running on the user's Windows machine.
You can call tools to get things done: run shell commands, read/write files in your workspace
folder ({DEFAULT_WORKSPACE}), fetch web pages, and search the web. Additional tools may be
provided by connected MCP servers.

Guidelines:
- Use tools when they help; answer directly when they don't.
- Prefer file tools over shell for reading/writing workspace files.
- Shell commands run in PowerShell and require the user's approval - keep them focused.
- Web page fetches and file writes outside the workspace also wait for the user's
  approval; batch related work to keep the number of approval prompts low.
- After using tools, summarize what you did and what you found."""


def resolve_approval(approval_id: str, approve: bool) -> bool:
    future = _pending_approvals.pop(approval_id, None)
    if future is None or future.done():
        return False
    future.set_result(approve)
    return True


async def _await_approval(approval_id: str) -> bool:
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending_approvals[approval_id] = future
    try:
        return await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT_S)
    except asyncio.TimeoutError:
        _pending_approvals.pop(approval_id, None)
        return False


async def collect_tools() -> list[Tool]:
    tools: list[Tool] = list(builtin_tools())
    try:
        from .mcp_client import mcp_tools
        tools.extend(await mcp_tools())
    except Exception:
        log.exception("Failed to load MCP tools; continuing with builtins")
    return tools


async def stream_agent_chat(
    conv: dict,
    provider: ModelProvider,
    params: Optional[GenParams] = None,
    gen: Optional[int] = None,
) -> AsyncIterator[dict]:
    from . import runs
    if gen is None:
        gen = runs.generation(conv["id"])
    tools = await collect_tools()
    tool_map = {t.name: t for t in tools}
    specs = [t.spec() for t in tools]

    if not conv.get("system_prompt"):
        prompt = AGENT_SYSTEM_PROMPT
        from .tools.files import allowed_roots
        extra = (await allowed_roots())[1:]
        if extra:
            prompt += (
                "\n\nThe user has also granted your file tools access to these folders"
                " (use absolute paths): " + "; ".join(str(p) for p in extra)
            )
        conv = {**conv, "system_prompt": prompt}
    history = await build_history(conv)

    for _step in range(MAX_AGENT_STEPS):
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        errored = False

        async for ev in provider.chat(conv["model"], history, tools=specs, params=params):
            if ev.type == "text_delta" and ev.text:
                text_parts.append(ev.text)
            elif ev.type == "tool_call" and ev.tool_call:
                tool_calls.append(ev.tool_call)
            elif ev.type == "error":
                errored = True
            if ev.type != "done":
                yield ev.model_dump(exclude_none=True)

        text = "".join(text_parts)
        if text or tool_calls:
            await persist_if_current(
                conv["id"], gen, "assistant", text,
                tool_calls=[tc.model_dump() for tc in tool_calls] or None,
            )
            history.append(Message(
                role="assistant", content=text, tool_calls=tool_calls or None))

        if errored or not tool_calls:
            break

        for tc in tool_calls:
            result = await _execute_tool_call(tool_map, tc)
            if result is None:
                # approval path: emit events through the generator
                approval_id = uuid.uuid4().hex
                yield {
                    "type": "approval_required",
                    "approval_id": approval_id,
                    "tool_call": tc.model_dump(),
                }
                approved = await _await_approval(approval_id)
                if approved:
                    result = await _run_tool(tool_map[tc.name], tc)
                else:
                    result = "The user denied this tool call."
            yield {
                "type": "tool_result",
                "tool_call_id": tc.id,
                "name": tc.name,
                "content": result,
            }
            await persist_if_current(conv["id"], gen, "tool", result, tool_call_id=tc.id)
            history.append(Message(role="tool", content=result, tool_call_id=tc.id))
    else:
        note = f"[Agent stopped: reached the {MAX_AGENT_STEPS}-step limit]"
        yield {"type": "text_delta", "text": "\n\n" + note}
        await persist_if_current(conv["id"], gen, "assistant", note)

    yield {"type": "done"}


async def _execute_tool_call(tool_map: dict[str, Tool], tc: ToolCall) -> Optional[str]:
    """Run a tool immediately, or return None when it needs the approval flow."""
    tool = tool_map.get(tc.name)
    if tool is None:
        return f"Error: unknown tool '{tc.name}'"
    if await tool.needs_approval(tc.arguments):
        return None
    return await _run_tool(tool, tc)


async def _run_tool(tool: Tool, tc: ToolCall) -> str:
    try:
        return await tool.run(tc.arguments)
    except Exception as exc:
        log.exception("Tool %s failed", tc.name)
        return f"Error running {tc.name}: {exc}"
