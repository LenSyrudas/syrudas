"""Chat orchestration: builds normalized history and streams provider events.

Events yielded here are dicts serialized as NDJSON lines by the chat route.
Event vocabulary sent to the frontend:
  meta, text_delta, tool_call, tool_result, approval_required, usage, error, done
"""
from __future__ import annotations

import json
import re
from typing import AsyncIterator, Optional

import logging

from . import db, runs
from .config import MAX_HISTORY_CHARS
from .providers.base import ModelProvider
from .schemas import GenParams, Message, StreamEvent, ToolCall

log = logging.getLogger(__name__)

# stands in for a tool result that was never recorded, so a tool_call always
# has an answer to point at
INTERRUPTED_TOOL_RESULT = "[interrupted: no result was recorded for this tool call]"


async def persist_if_current(conv_id: str, gen: int, role: str, content: str = "",
                             **kw) -> None:
    """Persist a message unless the history was rewritten since the stream
    started (rewind/delete) - a zombie stream must not orphan tool messages
    or resurrect rows in a deleted conversation."""
    if runs.generation(conv_id) != gen:
        log.info("Skipping stale write to conversation %s (role=%s)", conv_id[:8], role)
        return
    await db.add_message(conv_id, role, content, **kw)


async def build_history(conv: dict) -> list[Message]:
    messages: list[Message] = []
    if conv.get("system_prompt"):
        messages.append(Message(role="system", content=conv["system_prompt"]))
    for m in await db.list_messages(conv["id"]):
        tool_calls = None
        if m["tool_calls"]:
            tool_calls = [ToolCall(**tc) for tc in m["tool_calls"]]
        messages.append(Message(
            role=m["role"], content=m["content"] or "",
            tool_calls=tool_calls, tool_call_id=m["tool_call_id"],
        ))
    return trim_history(repair_tool_calls(messages))


def repair_tool_calls(messages: list[Message]) -> list[Message]:
    """Give every assistant tool_call an answering tool message.

    A run that died between persisting the assistant turn and persisting its
    tool results (a crash, or a Stop while a tool was still running) leaves
    tool_calls with nothing answering them. Both OpenAI and Anthropic reject
    that shape outright, so replaying it would 400 the conversation on every
    later turn - permanently, since the rows are already on disk. The agent
    loop closes the gap as it exits; this is the backstop for the rows it
    couldn't reach and for conversations damaged before it learned to.
    """
    out: list[Message] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        out.append(m)
        i += 1
        if m.role != "assistant" or not m.tool_calls:
            continue
        # this turn's results are the contiguous run of tool messages after it
        answered: set[str] = set()
        while i < len(messages) and messages[i].role == "tool":
            if messages[i].tool_call_id:
                answered.add(messages[i].tool_call_id)
            out.append(messages[i])
            i += 1
        # keep synthesized results inside that run: the Anthropic adapter
        # merges consecutive tool results into one user message and only
        # merges into the *preceding* block, so a stray result placed after
        # the next assistant turn would be orphaned all over again
        for tc in m.tool_calls:
            if tc.id not in answered:
                log.info("Answering unrecorded tool_call %s (%s)", tc.id, tc.name)
                out.append(Message(role="tool", content=INTERRUPTED_TOOL_RESULT,
                                   tool_call_id=tc.id))
    return out


def _msg_chars(m: Message) -> int:
    n = len(m.content or "")
    for tc in m.tool_calls or []:
        n += len(tc.name) + len(json.dumps(tc.arguments))
    return n


def trim_history(messages: list[Message], budget: int = MAX_HISTORY_CHARS) -> list[Message]:
    """Keep the system prompt plus as many of the newest messages as fit.

    Without this, long conversations overflow the model's context and the
    backend silently drops the OLDEST content - including the system prompt.
    The newest message is always kept even if it alone exceeds the budget.
    """
    if not messages:
        return messages
    system = messages[:1] if messages[0].role == "system" else []
    rest = messages[len(system):]

    kept: list[Message] = []
    total = sum(_msg_chars(m) for m in system)
    for m in reversed(rest):
        size = _msg_chars(m)
        if kept and total + size > budget:
            break
        kept.append(m)
        total += size
    kept.reverse()
    # a tool result whose assistant tool_call was trimmed away confuses
    # backends - drop stranded leading tool messages
    while kept and kept[0].role == "tool":
        kept.pop(0)
    if not kept:
        # everything kept was stranded tool output: fall back to the newest
        # non-tool message rather than resurrecting an orphan tool result
        for m in reversed(rest):
            if m.role != "tool":
                kept = [m]
                break
    return system + kept


async def stream_plain_chat(
    conv: dict,
    provider: ModelProvider,
    params: Optional[GenParams] = None,
    gen: Optional[int] = None,
) -> AsyncIterator[dict]:
    """Single completion, no tools. Persists the assistant reply when done."""
    if gen is None:
        gen = runs.generation(conv["id"])
    history = await build_history(conv)
    text_parts: list[str] = []
    usage: Optional[StreamEvent] = None
    async for ev in provider.chat(conv["model"], history, params=params):
        if ev.type == "text_delta" and ev.text:
            text_parts.append(ev.text)
        elif ev.type == "usage":
            usage = ev
        yield ev.model_dump(exclude_none=True)
    if text_parts:  # persist partial output even if the stream errored midway
        await persist_if_current(
            conv["id"], gen, "assistant", "".join(text_parts),
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
        )


def title_from(text: str, limit: int = 48) -> str:
    # attached-file blocks would make useless titles - use the typed text only
    text = re.sub(r'<file name="[^"]*">.*?</file>', "", text, flags=re.DOTALL)
    text = " ".join(text.split())
    if not text:
        text = "File attachment"
    return text if len(text) <= limit else text[: limit - 1] + "…"
