"""Agent access to the local knowledge index (see server/knowledge.py).

Read-only and sandboxed at index time, so the tool is ungated: it can only
surface passages from files the user chose to index.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import db, knowledge
from . import Tool, truncate

RESULT_LIMIT = 8000
MAX_K = 10


class KnowledgeSearchTool(Tool):
    name = "knowledge_search"
    description = (
        "Search the local knowledge index (files the user indexed under "
        "Settings > Knowledge) and return the most relevant passages with "
        "their source paths. Use before answering questions about indexed documents."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look for"},
            "k": {"type": "integer", "description": "How many passages (default 5, max 10)"},
        },
        "required": ["query"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        query = " ".join(str(args.get("query", "")).split())
        if not query:
            return "Error: empty query"
        try:
            k = max(1, min(int(args.get("k") or knowledge.SEARCH_K), MAX_K))
        except (TypeError, ValueError):
            k = knowledge.SEARCH_K
        try:
            results = await knowledge.search(query, k=k)
        except ValueError as exc:
            return f"Error: {exc}"
        if not results:
            if await db.count_knowledge_chunks() == 0:
                return ("The knowledge index is empty - the user can index files "
                        "under Settings > Knowledge.")
            return f"No indexed passages match {query!r}"
        blocks = [
            f"[{r['path']} #{r['seq']}] (score {r['score']})\n{r['content']}"
            for r in results
        ]
        return truncate("\n\n".join(blocks), RESULT_LIMIT)


async def knowledge_prompt_block() -> str:
    """One-liner for the agent prompt so the model knows the index exists."""
    sources = await db.list_knowledge_sources()
    if not sources:
        return ""
    names = ", ".join(Path(s["path"]).name for s in sources[:10])
    extra = f" and {len(sources) - 10} more" if len(sources) > 10 else ""
    return (
        f"A local knowledge index holds {len(sources)} indexed source(s): "
        f"{names}{extra}. Use knowledge_search to pull relevant passages "
        "before answering questions about these documents."
    )
