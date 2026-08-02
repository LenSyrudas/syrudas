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

# Per file: (size when last read, furthest contiguous character index seen).
#
# This tracks COVERAGE, not merely "was truncated", and the difference is what
# makes large files editable at all. The old record was a size only, so any
# write shorter than the file was refused - and file_read had no way to return
# anything but the first READ_LIMIT characters. A file over that limit could
# therefore never be edited: the refusal told the model to "re-read the file in
# full", which was not a thing it could do. Paging plus coverage closes that:
# once the model has actually seen every character, a shorter write is a
# legitimate edit rather than a data loss.
# file_write consults this: writing back content the model only partially saw
# silently destroys the remainder, which is the worst thing these tools can do.
# Module state is enough here - the app is a single process serving one user,
# and a stale entry only ever causes a refusal the model can recover from. The
# truncation marker in the content is the durable half of the pair, and covers
# the case this dict cannot: a write-back in a session after a restart.
_read_progress: dict[Path, tuple[int, int]] = {}


def _int_arg(args: dict[str, Any], key: str, default: int) -> int:
    """Read an integer argument, tolerating the string form models often send."""
    raw = args.get(key, default)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a whole number, got {raw!r}") from exc


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
        "absolute paths work in folders the user has granted access to. Long "
        "files come back in pieces: read from 'offset' to continue where the "
        "previous call stopped, until you have seen the whole file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative (workspace) or absolute path"},
            "offset": {
                "type": "integer",
                "description": "Character index to start at. Omit for the beginning.",
            },
            "limit": {
                "type": "integer",
                "description": f"Characters to return. Omit for the default {READ_LIMIT}.",
            },
        },
        "required": ["path"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        try:
            target = _resolve(str(args.get("path", "")), await allowed_roots())
            if not target.is_file():
                return f"Error: not a file: {args.get('path')}"
            offset = _int_arg(args, "offset", 0)
            limit = _int_arg(args, "limit", READ_LIMIT)
            if offset < 0:
                return "Error: offset must be zero or greater."
            if limit <= 0:
                return "Error: limit must be greater than zero."

            text = target.read_text("utf-8", errors="replace")
            total = len(text)
            if offset > total:
                return (f"Error: offset {offset} is past the end of {target.name}, "
                        f"which is {total} characters.")

            chunk = text[offset:offset + limit]
            end = offset + len(chunk)

            previous_total, seen = _read_progress.get(target, (total, 0))
            if previous_total != total:
                seen = 0  # the file changed since the last read; coverage restarts
            # Only a read that begins at or before the high-water mark extends
            # coverage. Jumping ahead leaves a hole, and a hole is unseen content.
            if offset <= seen:
                seen = max(seen, end)
            _read_progress[target] = (total, seen)

            if seen >= total:
                if offset == 0 and end == total:
                    return text
                return (chunk + f"\n[Characters {offset}-{end} of {total}. "
                        "You have now seen the whole file.]")
            return (chunk +
                    f"\n{TRUNCATION_MARK} characters {offset}-{end} of {total} shown]"
                    f"\n[Call file_read again with offset={end} for the next part. "
                    "Do not write this back as the whole file, and do not include "
                    "this bracketed note in anything you write.]")
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

            progress = _read_progress.get(target)
            if existed and progress:
                total, seen = progress
                # Only refuse while there is genuinely unseen content. Once the
                # model has paged through the whole file, a shorter write is an
                # ordinary edit - refusing it there is what made large files
                # uneditable rather than merely awkward.
                if seen < total and len(content) < total:
                    return (f"Error: refusing to write - you have seen {seen} of "
                            f"{total} characters of {target.name}, and this content "
                            f"is shorter ({len(content)}), so up to "
                            f"{total - len(content)} characters you never saw would "
                            f"be lost. Read the rest first with file_read(path="
                            f"\"{args.get('path')}\", offset={seen}) and continue "
                            "until it says you have seen the whole file, then write "
                            "it back - or write to a different path.")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, "utf-8")
            # what is on disk is exactly what was just written, and fully known
            _read_progress[target] = (len(content), len(content))
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
