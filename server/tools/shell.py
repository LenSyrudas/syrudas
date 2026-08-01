"""PowerShell tool - always gated behind per-call user approval in the UI."""
from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

from ..config import DEFAULT_WORKSPACE
from . import Tool, truncate

log = logging.getLogger(__name__)

OUTPUT_LIMIT = 8000
TIMEOUT_S = 120


def _kill_tree(proc) -> None:
    """Kill the shell AND whatever it started.

    proc.kill() ends only PowerShell itself, so `npm install`, a dev server or
    anything else it spawned survives - invisibly, because the process is
    created with CREATE_NO_WINDOW. taskkill /T walks the child tree; /F because
    a hung child will not go quietly.
    """
    if proc.returncode is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        log.warning("taskkill failed for PID %s; falling back to kill()", proc.pid)
    try:
        proc.kill()  # no-op if taskkill already got it
    except (OSError, ProcessLookupError):
        pass


class ShellTool(Tool):
    name = "shell"
    description = (
        "Run a PowerShell command on the user's Windows machine and return its output. "
        "The working directory is the agent workspace folder. Every call requires "
        "explicit user approval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The PowerShell command to run"},
        },
        "required": ["command"],
    }
    requires_approval = True

    async def run(self, args: dict[str, Any]) -> str:
        command = str(args.get("command", "")).strip()
        if not command:
            return "Error: empty command"
        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(DEFAULT_WORKSPACE),
                # no console flash when the parent is the windowed desktop app
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
        except asyncio.TimeoutError:
            _kill_tree(proc)
            return f"Error: command timed out after {TIMEOUT_S}s"
        except asyncio.CancelledError:
            # Stop, a closed tab, a dropped connection. Without this the
            # PowerShell process and anything it started keep running with no
            # window to see them in - CREATE_NO_WINDOW is set - and nothing
            # left that knows they exist.
            _kill_tree(proc)
            raise
        except OSError as exc:
            return f"Error: {exc}"
        text = out.decode("utf-8", "replace").strip()
        result = truncate(text or "(no output)", OUTPUT_LIMIT)
        return f"exit code: {proc.returncode}\n{result}"
