"""Schema migrations must work for any table, and refuse what they can't handle.

The previous mechanism read `PRAGMA table_info(messages)` and nothing else, so
it could only ever add a column to one table. The first column added to
conversations, documents, memories or settings would have shipped a build that
raised on every existing install and worked perfectly on the developer's own
machine - invisible to the author, certain for everyone upgrading.

Run: .venv\\Scripts\\python.exe scripts\\test_migrations.py
"""
import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import db  # noqa: E402


def fresh_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="syrudas-migrate-"))


async def open_at(path: Path):
    """Point the module at a database and open it, running migrations."""
    await db.close_db()
    db.DB_PATH = path
    return await db.get_db()


def version_of(path: Path) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def legacy_db(path: Path) -> None:
    """A database as an older build left it: no token columns, version 0."""
    con = sqlite3.connect(path)
    try:
        con.executescript("""
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT 'New chat',
                provider_id TEXT, model TEXT,
                agent_mode INTEGER NOT NULL DEFAULT 0,
                system_prompt TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
                tool_calls TEXT, tool_call_id TEXT, created_at TEXT NOT NULL);
            INSERT INTO conversations VALUES
                ('c1','irreplaceable','p','m',0,'','2026-01-01','2026-01-01');
            INSERT INTO messages VALUES ('m1','c1','user','keep me',NULL,NULL,'2026-01-01');
        """)
        con.commit()
    finally:
        con.close()


# --- tests ---

async def test_fresh_database_is_stamped():
    path = fresh_dir() / "new.db"
    await open_at(path)
    await db.close_db()

    assert version_of(path) == db.SCHEMA_VERSION, \
        f"a new database should open at v{db.SCHEMA_VERSION}, got v{version_of(path)}"
    assert not list(path.parent.glob("*pre-v*")), \
        "nothing existed to back up, so no backup should have been written"
    print(f"fresh database: opened and stamped at v{db.SCHEMA_VERSION}, no backup OK")


async def test_legacy_database_is_migrated_and_backed_up():
    d = fresh_dir()
    path = d / "syrudas.db"
    legacy_db(path)
    assert version_of(path) == 0

    conn = await open_at(path)
    cols = {r["name"] for r in await conn.execute_fetchall("PRAGMA table_info(messages)")}
    convs = await db.list_conversations()
    await db.close_db()

    assert {"input_tokens", "output_tokens"} <= cols, f"columns not added: {cols}"
    assert version_of(path) == db.SCHEMA_VERSION
    assert [c["title"] for c in convs] == ["irreplaceable"], "existing data was lost"
    backups = list(d.glob("*pre-v0*"))
    assert backups, "a database with data in it must be copied before migrating"
    assert version_of(backups[0]) == 0, "the backup should be the pre-migration file"
    print(f"legacy database: migrated, data intact, backed up to {backups[0].name} OK")


async def test_a_current_database_is_left_alone():
    d = fresh_dir()
    path = d / "syrudas.db"
    await open_at(path)
    await db.close_db()
    for f in d.glob("*pre-v*"):
        f.unlink()

    await open_at(path)          # second open, already current
    await db.close_db()

    assert not list(d.glob("*pre-v*")), \
        "reopening an up-to-date database must not migrate or back it up again"
    print("already-current database: no migration, no second backup OK")


async def test_a_future_database_is_refused():
    d = fresh_dir()
    path = d / "syrudas.db"
    await open_at(path)
    await db.close_db()

    con = sqlite3.connect(path)          # as a newer build would leave it
    con.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 5}")
    con.commit()
    con.close()

    try:
        await open_at(path)
        raise AssertionError("opening a newer database should have been refused")
    except RuntimeError as exc:
        assert "newer version" in str(exc), exc
        assert str(db.SCHEMA_VERSION) in str(exc), "the error should name what this build knows"
    finally:
        await db.close_db()
    print("future database: refused rather than written over OK")


async def test_a_migration_can_touch_any_table():
    """The actual defect: the old check could only ever see `messages`."""
    d = fresh_dir()
    path = d / "syrudas.db"
    await open_at(path)
    await db.close_db()

    async def add_a_column_to_conversations(conn):
        cols = {r["name"] for r in
                await conn.execute_fetchall("PRAGMA table_info(conversations)")}
        if "pinned" not in cols:
            await conn.execute("ALTER TABLE conversations ADD COLUMN pinned INTEGER")

    db.MIGRATIONS.append(add_a_column_to_conversations)
    db.SCHEMA_VERSION = len(db.MIGRATIONS)
    try:
        conn = await open_at(path)
        cols = {r["name"] for r in
                await conn.execute_fetchall("PRAGMA table_info(conversations)")}
        await db.close_db()
        assert "pinned" in cols, \
            "a migration against a table other than `messages` did not run"
        assert version_of(path) == db.SCHEMA_VERSION
        assert list(d.glob("*pre-v1*")), "an upgrade of existing data must be backed up"
    finally:
        db.MIGRATIONS.pop()
        db.SCHEMA_VERSION = len(db.MIGRATIONS)
    print("a migration on `conversations` runs and stamps - the old check could not OK")


async def main():
    try:
        await test_fresh_database_is_stamped()
        await test_legacy_database_is_migrated_and_backed_up()
        await test_a_current_database_is_left_alone()
        await test_a_future_database_is_refused()
        await test_a_migration_can_touch_any_table()
    finally:
        await db.close_db()
    print("\nALL MIGRATION TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
