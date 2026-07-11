"""SQLite persistence: conversations, messages, provider instances, MCP servers, settings."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

from .config import DB_PATH

_conn: Optional[aiosqlite.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    provider_id TEXT,
    model TEXT,
    agent_mode INTEGER NOT NULL DEFAULT 0,
    system_prompt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    tool_call_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS provider_instances (
    id TEXT PRIMARY KEY,
    type_id TEXT NOT NULL,
    name TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '[]',
    env TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


async def get_db() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        _conn = await aiosqlite.connect(DB_PATH)
        _conn.row_factory = aiosqlite.Row
        await _conn.execute("PRAGMA foreign_keys = ON")
        await _conn.executescript(SCHEMA)
        await _conn.commit()
    return _conn


async def close_db() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


# --- conversations ---

async def create_conversation(provider_id: str, model: str, agent_mode: bool,
                              system_prompt: str = "") -> dict:
    db = await get_db()
    conv = {
        "id": new_id(), "title": "New chat", "provider_id": provider_id, "model": model,
        "agent_mode": int(agent_mode), "system_prompt": system_prompt,
        "created_at": now(), "updated_at": now(),
    }
    await db.execute(
        "INSERT INTO conversations (id,title,provider_id,model,agent_mode,system_prompt,created_at,updated_at)"
        " VALUES (:id,:title,:provider_id,:model,:agent_mode,:system_prompt,:created_at,:updated_at)",
        conv,
    )
    await db.commit()
    return conv


async def list_conversations() -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM conversations ORDER BY updated_at DESC")
    return [dict(r) for r in rows]


async def get_conversation(conv_id: str) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM conversations WHERE id = ?", (conv_id,))
    return dict(rows[0]) if rows else None


async def update_conversation(conv_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    db = await get_db()
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    await db.execute(f"UPDATE conversations SET {sets} WHERE id = :_id", {**fields, "_id": conv_id})
    await db.commit()


async def delete_conversation(conv_id: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    await db.commit()


# --- messages ---

async def add_message(conversation_id: str, role: str, content: str = "",
                      tool_calls: Optional[list[dict]] = None,
                      tool_call_id: Optional[str] = None) -> dict:
    db = await get_db()
    msg = {
        "id": new_id(), "conversation_id": conversation_id, "role": role, "content": content,
        "tool_calls": json.dumps(tool_calls) if tool_calls else None,
        "tool_call_id": tool_call_id, "created_at": now(),
    }
    await db.execute(
        "INSERT INTO messages (id,conversation_id,role,content,tool_calls,tool_call_id,created_at)"
        " VALUES (:id,:conversation_id,:role,:content,:tool_calls,:tool_call_id,:created_at)",
        msg,
    )
    await db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?",
                     (now(), conversation_id))
    await db.commit()
    return msg


async def list_messages(conversation_id: str) -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid",
        (conversation_id,),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["tool_calls"] = json.loads(d["tool_calls"]) if d["tool_calls"] else None
        out.append(d)
    return out


async def get_last_user_message(conversation_id: str) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM messages WHERE conversation_id = ? AND role = 'user'"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (conversation_id,),
    )
    return dict(rows[0]) if rows else None


async def get_messages_after(conversation_id: str, message_id: str) -> list[dict]:
    """Raw message rows (tool_calls still JSON text) after the given message."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT rowid FROM messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    )
    if not rows:
        return []
    out = await db.execute_fetchall(
        "SELECT * FROM messages WHERE conversation_id = ? AND rowid > ? ORDER BY rowid",
        (conversation_id, rows[0]["rowid"]),
    )
    return [dict(r) for r in out]


async def restore_messages(rows: list[dict]) -> None:
    """Reinsert raw rows captured by get_messages_after (regenerate rollback)."""
    if not rows:
        return
    db = await get_db()
    for r in rows:
        await db.execute(
            "INSERT OR IGNORE INTO messages (id,conversation_id,role,content,tool_calls,tool_call_id,created_at)"
            " VALUES (:id,:conversation_id,:role,:content,:tool_calls,:tool_call_id,:created_at)",
            r,
        )
    await db.commit()


async def delete_messages_from(conversation_id: str, message_id: str,
                               inclusive: bool) -> int:
    """Delete a message (optionally) and everything after it in the conversation."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT rowid FROM messages WHERE id = ? AND conversation_id = ?",
        (message_id, conversation_id),
    )
    if not rows:
        return 0
    pivot = rows[0]["rowid"]
    op = ">=" if inclusive else ">"
    cursor = await db.execute(
        f"DELETE FROM messages WHERE conversation_id = ? AND rowid {op} ?",  # noqa: S608
        (conversation_id, pivot),
    )
    await db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?",
                     (now(), conversation_id))
    await db.commit()
    return cursor.rowcount


# --- provider instances ---

async def create_provider_instance(type_id: str, name: str, config: dict) -> dict:
    db = await get_db()
    inst = {"id": new_id(), "type_id": type_id, "name": name,
            "config": json.dumps(config), "created_at": now()}
    await db.execute(
        "INSERT INTO provider_instances (id,type_id,name,config,created_at)"
        " VALUES (:id,:type_id,:name,:config,:created_at)", inst)
    await db.commit()
    inst["config"] = config
    return inst


async def list_provider_instances() -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM provider_instances ORDER BY created_at")
    out = []
    for r in rows:
        d = dict(r)
        d["config"] = json.loads(d["config"])
        out.append(d)
    return out


async def get_provider_instance(inst_id: str) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM provider_instances WHERE id = ?", (inst_id,))
    if not rows:
        return None
    d = dict(rows[0])
    d["config"] = json.loads(d["config"])
    return d


async def update_provider_instance(inst_id: str, name: str, config: dict) -> None:
    db = await get_db()
    await db.execute("UPDATE provider_instances SET name = ?, config = ? WHERE id = ?",
                     (name, json.dumps(config), inst_id))
    await db.commit()


async def delete_provider_instance(inst_id: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM provider_instances WHERE id = ?", (inst_id,))
    await db.commit()


# --- MCP servers ---

async def create_mcp_server(name: str, command: str, args: list[str], env: dict) -> dict:
    db = await get_db()
    row = {"id": new_id(), "name": name, "command": command,
           "args": json.dumps(args), "env": json.dumps(env), "enabled": 1}
    await db.execute(
        "INSERT INTO mcp_servers (id,name,command,args,env,enabled)"
        " VALUES (:id,:name,:command,:args,:env,:enabled)", row)
    await db.commit()
    row["args"], row["env"] = args, env
    return row


async def list_mcp_servers() -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM mcp_servers")
    out = []
    for r in rows:
        d = dict(r)
        d["args"] = json.loads(d["args"])
        d["env"] = json.loads(d["env"])
        out.append(d)
    return out


async def update_mcp_server(server_id: str, **fields: Any) -> None:
    if not fields:
        return
    for k in ("args", "env"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    db = await get_db()
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    await db.execute(f"UPDATE mcp_servers SET {sets} WHERE id = :_id", {**fields, "_id": server_id})
    await db.commit()


async def delete_mcp_server(server_id: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
    await db.commit()


# --- settings ---

async def get_setting(key: str, default: str = "") -> str:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT value FROM settings WHERE key = ?", (key,))
    return rows[0]["value"] if rows else default


async def set_setting(key: str, value: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO settings (key,value) VALUES (?,?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    await db.commit()
