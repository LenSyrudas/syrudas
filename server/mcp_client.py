"""MCP client: connects configured stdio servers and exposes their tools to the agent.

Each server connection lives in a dedicated owner task because anyio cancel
scopes must be entered and exited by the same task; other tasks only use the
initialized session object.
"""
from __future__ import annotations

import asyncio
import json
import time
import logging
import re
import shutil
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from . import db
from .tools import Tool, truncate

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 90  # first npx run may download the package
CALL_TIMEOUT_S = 120
RESULT_LIMIT = 12000


def _win_params(command: str, args: list[str], env: dict[str, str]) -> StdioServerParameters:
    """Launch .cmd/.bat shims (npx, npm) through cmd /c on Windows."""
    resolved = shutil.which(command)
    if resolved and resolved.lower().endswith((".cmd", ".bat")):
        args = ["/c", command, *args]
        command = "cmd"
    return StdioServerParameters(
        command=command, args=args, env={**get_default_environment(), **env})


class _Conn:
    """One live stdio server connection, owned by a background task."""

    def __init__(self, server: dict):
        self.server = server
        self.config_key = _config_key(server)
        self.session: Optional[ClientSession] = None
        self.error: Optional[str] = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            params = _win_params(
                self.server["command"], self.server["args"], self.server["env"])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.session = session
                    self._ready.set()
                    await self._stop.wait()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("MCP server %r failed: %s", self.server["name"], self.error)
        finally:
            self.session = None
            self._ready.set()

    async def wait_ready(self) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout=CONNECT_TIMEOUT_S)

    @property
    def alive(self) -> bool:
        return self.session is not None and not self._task.done()

    async def close(self) -> None:
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()


def _config_key(server: dict) -> str:
    return json.dumps(
        [server["command"], server["args"], server["env"]], sort_keys=True)


_conns: dict[str, _Conn] = {}

# server id -> monotonic time before which not to retry a failed connection.
# Without this a dead or misconfigured server was reconnected on EVERY message,
# and because the connect timeout is 90s and the loop is serial, one bad entry
# added a minute and a half of silence before the first token, every time.
FAILURE_COOLDOWN_S = 120
_cooldown: dict[str, float] = {}


async def _get_conn(server: dict) -> Optional[_Conn]:
    until = _cooldown.get(server["id"])
    if until and time.monotonic() < until:
        return None
    conn = _conns.get(server["id"])
    if conn and (not conn.alive and conn._ready.is_set() or conn.config_key != _config_key(server)):
        await conn.close()
        conn = None
    if conn is None:
        conn = _Conn(server)
        _conns[server["id"]] = conn
    try:
        await conn.wait_ready()
    except asyncio.TimeoutError:
        conn.error = f"timed out after {CONNECT_TIMEOUT_S}s waiting for server start"
    if conn.session is None:
        log.warning("MCP server %r unavailable (%s); not retrying for %ds",
                    server["name"], conn.error, FAILURE_COOLDOWN_S)
        _cooldown[server["id"]] = time.monotonic() + FAILURE_COOLDOWN_S
        return None
    _cooldown.pop(server["id"], None)
    return conn


async def close_all() -> None:
    for conn in list(_conns.values()):
        await conn.close()
    _conns.clear()
    _cooldown.clear()


def _sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


class McpTool(Tool):
    # Gated by default. An MCP server runs with the user's privileges and can
    # expose anything - a filesystem server's write, a shell wrapper, an API
    # client - and the app cannot tell which from the tool schema alone.
    # Registering a server should not silently widen what the model may do
    # without asking, which is the promise every other risky tool here keeps.
    requires_approval = True

    def __init__(self, conn: _Conn, remote_name: str, description: str, parameters: dict):
        self._conn = conn
        self._remote_name = remote_name
        self.name = _sanitize(f"{conn.server['name']}_{remote_name}")
        self.description = f"[MCP:{conn.server['name']}] {description or remote_name}"
        self.parameters = parameters or {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any]) -> str:
        if not self._conn.alive:
            return f"Error: MCP server '{self._conn.server['name']}' is not connected"
        try:
            result = await asyncio.wait_for(
                self._conn.session.call_tool(self._remote_name, args or {}),
                timeout=CALL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return f"Error: MCP tool call timed out after {CALL_TIMEOUT_S}s"
        except Exception as exc:
            return f"Error calling MCP tool: {exc}"
        parts = []
        for block in result.content:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else f"[{getattr(block, 'type', 'content')}]")
        text = "\n".join(parts) or "(empty result)"
        if getattr(result, "isError", False):
            text = f"Error: {text}"
        return truncate(text, RESULT_LIMIT)


async def mcp_tools() -> list[Tool]:
    """Tools from every enabled MCP server; unreachable servers are skipped."""
    tools: list[Tool] = []
    for server in await db.list_mcp_servers():
        if not server["enabled"]:
            continue
        conn = await _get_conn(server)
        if conn is None:
            continue
        try:
            listing = await asyncio.wait_for(conn.session.list_tools(), timeout=30)
        except Exception as exc:
            log.warning("list_tools failed for %r: %s", server["name"], exc)
            continue
        for t in listing.tools:
            tools.append(McpTool(conn, t.name, t.description or "", t.inputSchema))
    return tools
