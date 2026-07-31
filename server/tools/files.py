"""File tools sandboxed to the agent workspace plus user-granted folders.

Relative paths resolve inside the workspace. Absolute paths are allowed only
inside folders the user granted under Settings -> Agent file access.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import DEFAULT_WORKSPACE
from . import Tool, TRUNCATION_MARK, truncate

READ_LIMIT = 12000

# Files handed to the model in truncated form, and how large they really were.
# file_write consults this: writing back content the model only partially saw
# silently destroys the remainder, which is the worst thing these tools can do.
# Module state is enough here - the app is a single process serving one user,
# and a stale entry only ever causes a refusal the model can recover from.
_truncated_reads: dict[Path, int] = {}


def _describe_change(target: Path, existed: bool, before: str, after: str) -> str:
    """Say what an overwrite actually did, in the terms a reviewer would check."""
    if not existed:
        return f"Created {target} ({len(after)} chars, {after.count(chr(10)) + 1} lines)"
    before_lines, after_lines = before.count("\n") + 1, after.count("\n") + 1
    note = (f"Wrote {len(after)} chars to {target} "
            f"(lines: {before_lines} -> {after_lines}, "
            f"bytes: {len(before)} -> {len(after)})")
    if len(after) < len(before) * 0.5:
        note += f"\nNote: this replaced more than half the file's previous content."
    return note


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
            text = target.read_text("utf-8", errors="replace")
            if len(text) > READ_LIMIT:
                # remember, so a later write-back of this partial view is refused
                _truncated_reads[target] = len(text)
                return (truncate(text, READ_LIMIT) +
                        "\n[You have seen the first "
                        f"{READ_LIMIT} of {len(text)} characters. Do NOT write this "
                        "back as the whole file - the rest would be lost.]")
            _truncated_reads.pop(target, None)  # a full read supersedes any partial one
            return text
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

    async def needs_approval(self, args: dict[str, Any]) -> bool:
        """Workspace writes are free; writes to user-granted folders are gated."""
        roots = await allowed_roots()
        try:
            target = _resolve(str(args.get("path", "")), roots)
        except ValueError:
            return False  # run() will refuse it anyway - no point prompting
        workspace = roots[0]
        return target != workspace and workspace not in target.parents

    async def run(self, args: dict[str, Any]) -> str:
        try:
            target = _resolve(str(args.get("path", "")), await allowed_roots())

            # A missing key is a malformed call, not a request to empty the file.
            # This used to coerce to "" and silently truncate to zero bytes.
            if "content" not in args or args["content"] is None:
                return ("Error: no 'content' given. Pass the full text to write; "
                        "pass an empty string only if you really mean to empty the file.")
            content = str(args["content"])

            existed = target.is_file()
            before = target.read_text("utf-8", errors="replace") if existed else ""

            if TRUNCATION_MARK in content:
                return ("Error: refusing to write - this content still contains a "
                        "truncation marker, so it is a partial view of a larger "
                        "result. Writing it back would discard everything after "
                        "the cut. Write only the part you intend to change, to a "
                        "new file, or ask for the region you need.")

            full_size = _truncated_reads.get(target)
            if existed and full_size and len(content) < full_size:
                return (f"Error: refusing to write - {target.name} was read in "
                        f"truncated form ({READ_LIMIT} of {full_size} chars shown) "
                        f"and this content is shorter ({len(content)} chars), so "
                        f"{full_size - len(content)} characters you never saw would "
                        "be lost. Re-read the file in full first, or write to a "
                        "different path.")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, "utf-8")
            _truncated_reads.pop(target, None)  # the file on disk is now what was written
            return _describe_change(target, existed, before, content)
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
