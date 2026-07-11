"""PowerShell tool - always gated behind per-call user approval in the UI."""
from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from ..config import DEFAULT_WORKSPACE
from . import Tool, truncate

OUTPUT_LIMIT = 8000
TIMEOUT_S = 120


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
            proc.kill()
            return f"Error: command timed out after {TIMEOUT_S}s"
        except OSError as exc:
            return f"Error: {exc}"
        text = out.decode("utf-8", "replace").strip()
        result = truncate(text or "(no output)", OUTPUT_LIMIT)
        return f"exit code: {proc.returncode}\n{result}"
