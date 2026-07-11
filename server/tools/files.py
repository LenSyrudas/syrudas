"""File tools sandboxed to the agent workspace plus user-granted folders.

Relative paths resolve inside the workspace. Absolute paths are allowed only
inside folders the user granted under Settings -> Agent file access.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import DEFAULT_WORKSPACE
from . import Tool, truncate

READ_LIMIT = 12000


async def allowed_roots() -> list[Path]:
    """Workspace first, then every configured folder that still exists."""
    from ..routes.settings import get_agent_folders

    roots = [DEFAULT_WORKSPACE.resolve()]
    for folder in await get_agent_folders():
        path = Path(folder)
        if path.is_dir():
            resolved = path.resolve()
            if resolved not in roots:
                roots.append(resolved)
    return roots


def _resolve(path_str: str, roots: list[Path]) -> Path:
    """Resolve a path against the sandbox; refuse anything outside it."""
    raw = Path(path_str) if path_str else Path(".")
    if raw.is_absolute():
        target = raw.resolve()
        for root in roots:
            if target == root or root in target.parents:
                return target
        allowed = ", ".join(str(r) for r in roots)
        raise ValueError(f"Path not in an allowed folder ({allowed}): {path_str}")
    target = (roots[0] / raw).resolve()
    if target != roots[0] and roots[0] not in target.parents:
        raise ValueError(f"Path escapes the workspace: {path_str}")
    return target


class FileReadTool(Tool):
    name = "file_read"
    description = (
        "Read a text file. Relative paths are inside the agent workspace; "
        "absolute paths work in folders the user has granted access to."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative (workspace) or absolute path"},
        },
        "required": ["path"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        try:
            target = _resolve(str(args.get("path", "")), await allowed_roots())
            if not target.is_file():
                return f"Error: not a file: {args.get('path')}"
            return truncate(target.read_text("utf-8", errors="replace"), READ_LIMIT)
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"


class FileWriteTool(Tool):
    name = "file_write"
    description = (
        "Write (create or overwrite) a text file. Relative paths are inside the "
        "agent workspace; absolute paths work in folders the user has granted access to."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative (workspace) or absolute path"},
            "content": {"type": "string", "description": "The full file content to write"},
        },
        "required": ["path", "content"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        try:
            target = _resolve(str(args.get("path", "")), await allowed_roots())
            target.parent.mkdir(parents=True, exist_ok=True)
            content = str(args.get("content", ""))
            target.write_text(content, "utf-8")
            return f"Wrote {len(content)} chars to {target}"
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"


class FileListTool(Tool):
    name = "file_list"
    description = (
        "List files and folders. Omit path (or use relative) for the agent workspace; "
        "absolute paths work in folders the user has granted access to."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative (workspace) or absolute path; omit for workspace root"},
        },
    }

    async def run(self, args: dict[str, Any]) -> str:
        try:
            target = _resolve(str(args.get("path", "") or "."), await allowed_roots())
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
