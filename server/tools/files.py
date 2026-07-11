"""File tools sandboxed to the agent workspace directory."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import DEFAULT_WORKSPACE
from . import Tool, truncate

READ_LIMIT = 12000


def _resolve(rel_path: str) -> Path:
    """Resolve a path inside the workspace; refuse escapes."""
    base = DEFAULT_WORKSPACE.resolve()
    target = (base / rel_path).resolve()
    if base != target and base not in target.parents:
        raise ValueError(f"Path escapes the workspace: {rel_path}")
    return target


class FileReadTool(Tool):
    name = "file_read"
    description = "Read a text file from the agent workspace folder."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace folder"},
        },
        "required": ["path"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        try:
            target = _resolve(str(args.get("path", "")))
            if not target.is_file():
                return f"Error: not a file: {args.get('path')}"
            return truncate(target.read_text("utf-8", errors="replace"), READ_LIMIT)
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"


class FileWriteTool(Tool):
    name = "file_write"
    description = "Write (create or overwrite) a text file in the agent workspace folder."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace folder"},
            "content": {"type": "string", "description": "The full file content to write"},
        },
        "required": ["path", "content"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        try:
            target = _resolve(str(args.get("path", "")))
            target.parent.mkdir(parents=True, exist_ok=True)
            content = str(args.get("content", ""))
            target.write_text(content, "utf-8")
            return f"Wrote {len(content)} chars to {target.name}"
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"


class FileListTool(Tool):
    name = "file_list"
    description = "List files and folders in the agent workspace folder (or a subfolder of it)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Subfolder relative to the workspace; omit for the root"},
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        try:
            target = _resolve(str(args.get("path", "") or "."))
            if not target.is_dir():
                return f"Error: not a folder: {args.get('path')}"
            lines = []
            for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                kind = "dir " if entry.is_dir() else "file"
                size = "" if entry.is_dir() else f"  {entry.stat().st_size} bytes"
                lines.append(f"{kind}  {entry.name}{size}")
            return "\n".join(lines) if lines else "(empty folder)"
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"
