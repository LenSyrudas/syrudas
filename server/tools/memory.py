"""Agent memory: short durable facts that persist across conversations.

Memories live in the local SQLite DB, are injected (newest first, within a
character budget) into the agent system prompt, and are fully user-visible
and deletable under Settings -> Agent memory. Saving is deliberately ungated:
it has no effect outside the app, every save shows up as a tool card in the
conversation, and the Settings page is the always-available kill switch.
Plain (non-agent) chat never sees memories.
"""
from __future__ import annotations

from typing import Any

from .. import db
from . import Tool

MAX_MEMORY_CHARS = 500     # memories are distilled facts, not documents
MAX_MEMORIES = 200         # hard cap so the store can't grow unbounded
PROMPT_BUDGET_CHARS = 4000  # newest-first slice injected into the agent prompt


def _clean(text: str) -> str:
    return " ".join(text.split())


def _format(mems: list[dict]) -> str:
    return "\n".join(f"- [{m['id']}] {m['content']}" for m in mems)


class MemorySaveTool(Tool):
    name = "memory_save"
    description = (
        "Remember a short durable fact across conversations (user preferences, "
        "project context, decisions). One distilled sentence per memory. "
        "Never save secrets, credentials, or transient details."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The fact to remember, one sentence"},
        },
        "required": ["content"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        content = _clean(str(args.get("content", "")))
        if not content:
            return "Error: empty content"
        if len(content) > MAX_MEMORY_CHARS:
            return f"Error: memory too long ({len(content)} chars, max {MAX_MEMORY_CHARS}) - distill it"
        try:
            mem = await db.add_memory(content, cap=MAX_MEMORIES)
        except ValueError:
            return (f"Error: memory is full ({MAX_MEMORIES} entries). "
                    "Delete outdated memories with memory_delete first.")
        return f"Saved memory [{mem['id']}]"


class MemoryDeleteTool(Tool):
    name = "memory_delete"
    description = "Forget a saved memory by its id (shown in brackets in the memory list)."
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The memory id to delete"},
        },
        "required": ["id"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        mem_id = str(args.get("id", "")).strip().strip("[]")
        if not mem_id:
            return "Error: empty id"
        if await db.delete_memory(mem_id):
            return f"Deleted memory [{mem_id}]"
        return f"Error: no memory with id [{mem_id}]"


class MemorySearchTool(Tool):
    name = "memory_search"
    description = (
        "Search saved memories by keyword. Useful when the memory list in the "
        "system prompt notes that older memories were not shown."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keyword or phrase to look for"},
        },
        "required": ["query"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        query = _clean(str(args.get("query", "")))
        if not query:
            return "Error: empty query"
        mems = await db.search_memories(query)
        if not mems:
            return f"No memories matching {query!r}"
        return _format(mems[:50])


async def memory_prompt_block() -> str:
    """Newest memories first, capped to PROMPT_BUDGET_CHARS, for the agent prompt."""
    mems = await db.list_memories()
    if not mems:
        return ""
    shown: list[dict] = []
    used = 0
    for m in mems:
        line_len = len(m["id"]) + len(m["content"]) + 6  # "- [id] content\n"
        if shown and used + line_len > PROMPT_BUDGET_CHARS:
            break
        shown.append(m)
        used += line_len
    block = ("Saved memories from earlier conversations (the user can review and "
             "delete these under Settings > Agent memory):\n" + _format(shown))
    hidden = len(mems) - len(shown)
    if hidden > 0:
        block += f"\n({hidden} older memories not shown - memory_search finds them)"
    return block
