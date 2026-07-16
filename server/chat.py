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
    return trim_history(messages)


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
