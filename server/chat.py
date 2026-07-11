"""Chat orchestration: builds normalized history and streams provider events.

Events yielded here are dicts serialized as NDJSON lines by the chat route.
Event vocabulary sent to the frontend:
  meta, text_delta, tool_call, tool_result, approval_required, usage, error, done
"""
from __future__ import annotations

from typing import AsyncIterator, Optional

from . import db
from .providers.base import ModelProvider
from .schemas import GenParams, Message, ToolCall


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
    return messages


async def stream_plain_chat(
    conv: dict,
    provider: ModelProvider,
    params: Optional[GenParams] = None,
) -> AsyncIterator[dict]:
    """Single completion, no tools. Persists the assistant reply when done."""
    history = await build_history(conv)
    text_parts: list[str] = []
    async for ev in provider.chat(conv["model"], history, params=params):
        if ev.type == "text_delta" and ev.text:
            text_parts.append(ev.text)
        yield ev.model_dump(exclude_none=True)
    if text_parts:  # persist partial output even if the stream errored midway
        await db.add_message(conv["id"], "assistant", "".join(text_parts))


def title_from(text: str, limit: int = 48) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
