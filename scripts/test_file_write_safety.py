"""file_write must not destroy content the model never saw.

file_read truncates at READ_LIMIT. Writing that partial view back used to
overwrite the whole file, discarding everything after the cut, ungated, and
reporting success - measured at 86,588 bytes lost on a 99 KB file. A missing
'content' key coerced to "" and emptied the file outright.

Two independent guards, because either can be the only one that fires:
- the shared truncation marker still present in the content (catches a
  write-back even in a later session, when nothing was recorded)
- a record of which files were handed over truncated, and how big they really
  were (catches a write-back with the marker edited out)

Run: .venv\\Scripts\\python.exe scripts\\test_file_write_safety.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-write-"))
WORKSPACE = TMP / "workspace"
WORKSPACE.mkdir()

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.tools import files as files_mod  # noqa: E402
files_mod.DEFAULT_WORKSPACE = WORKSPACE

from server.tools import TRUNCATION_MARK  # noqa: E402
from server.tools.files import READ_LIMIT, FileReadTool, FileWriteTool  # noqa: E402

READ, WRITE = FileReadTool(), FileWriteTool()


def big_file(name: str, lines: int = 3000) -> Path:
    p = WORKSPACE / name
    p.write_text("\n".join(f"line {i:05d} " + "x" * 20 for i in range(lines)), "utf-8")
    return p


async def test_truncated_readback_is_refused():
    target = big_file("big.md")
    original = target.stat().st_size
    seen = await READ.run({"path": "big.md"})
    assert TRUNCATION_MARK in seen, "precondition: the read must have been truncated"

    result = await WRITE.run({"path": "big.md", "content": seen})

    assert result.startswith("Error:"), f"expected a refusal, got {result!r}"
    assert target.stat().st_size == original, \
        f"file changed: {original} -> {target.stat().st_size}"
    print(f"truncated read-back: refused, all {original:,} bytes intact OK")


async def test_readback_with_the_marker_stripped_is_still_refused():
    """The marker is editable; the recorded original size is not."""
    target = big_file("big2.md")
    original = target.stat().st_size
    seen = await READ.run({"path": "big2.md"})
    doctored = seen.split(TRUNCATION_MARK)[0] + "\nedited tail\n"
    assert TRUNCATION_MARK not in doctored

    result = await WRITE.run({"path": "big2.md", "content": doctored})

    assert result.startswith("Error:"), f"expected a refusal, got {result!r}"
    assert "never saw" in result
    assert target.stat().st_size == original
    print("marker stripped but content shorter than the real file: refused OK")


async def test_the_marker_guard_stands_on_its_own():
    """The case the marker exists for: nothing recorded, e.g. a later session.

    The size guard cannot help here - it has no memory of the file being read
    truncated - so this pins the marker check rather than letting the other
    guard cover for it.
    """
    target = big_file("big4.md")
    original = target.stat().st_size
    seen = await READ.run({"path": "big4.md"})
    files_mod._truncated_reads.clear()  # as if the read happened before a restart

    result = await WRITE.run({"path": "big4.md", "content": seen})

    assert result.startswith("Error:"), f"expected a refusal, got {result!r}"
    assert "truncation marker" in result, result
    assert target.stat().st_size == original
    print("marker alone, with nothing recorded: refused OK")


async def test_missing_content_key_is_refused():
    target = WORKSPACE / "notes.md"
    target.write_text("important" * 100, "utf-8")
    before = target.stat().st_size

    result = await WRITE.run({"path": "notes.md"})

    assert result.startswith("Error:"), f"expected a refusal, got {result!r}"
    assert target.stat().st_size == before, "the file was modified anyway"
    print(f"missing 'content' key: refused, {before} bytes intact OK")


async def test_explicit_empty_string_is_allowed():
    """Refusing a missing key must not refuse a deliberate empty write."""
    target = WORKSPACE / "clear.md"
    target.write_text("something", "utf-8")

    result = await WRITE.run({"path": "clear.md", "content": ""})

    assert not result.startswith("Error:"), f"an explicit empty write must work: {result}"
    assert target.read_text("utf-8") == ""
    print("explicit empty string: allowed, file emptied as asked OK")


async def test_ordinary_writes_still_work_and_report_the_change():
    created = await WRITE.run({"path": "new.txt", "content": "hello"})
    assert created.startswith("Created"), created

    grown = await WRITE.run({"path": "new.txt", "content": "one\ntwo\nthree"})
    assert "lines: 1 -> 3" in grown, grown
    assert "bytes: 5 -> 13" in grown, grown

    shrunk = await WRITE.run({"path": "new.txt", "content": "x"})
    assert "more than half" in shrunk, f"a large shrink should be called out: {shrunk}"
    print("ordinary writes: allowed, and the change is reported OK")


async def test_a_full_read_clears_the_record():
    target = WORKSPACE / "small.md"
    target.write_text("y" * (READ_LIMIT + 500), "utf-8")
    await READ.run({"path": "small.md"})          # truncated, recorded
    target.write_text("short enough now", "utf-8")
    await READ.run({"path": "small.md"})          # full read supersedes it

    result = await WRITE.run({"path": "small.md", "content": "rewritten"})

    assert not result.startswith("Error:"), \
        f"a stale truncation record must not block a file since read in full: {result}"
    print("a later full read clears the truncated record OK")


async def test_a_different_path_is_unaffected():
    big_file("big3.md")
    seen = await READ.run({"path": "big3.md"})
    stripped = seen.split(TRUNCATION_MARK)[0]

    result = await WRITE.run({"path": "excerpt.md", "content": stripped})

    assert not result.startswith("Error:"), \
        f"writing an excerpt to a NEW path is legitimate: {result}"
    print("writing a partial view to a different path: allowed OK")


async def main():
    try:
        await test_truncated_readback_is_refused()
        await test_readback_with_the_marker_stripped_is_still_refused()
        await test_the_marker_guard_stands_on_its_own()
        await test_missing_content_key_is_refused()
        await test_explicit_empty_string_is_allowed()
        await test_ordinary_writes_still_work_and_report_the_change()
        await test_a_full_read_clears_the_record()
        await test_a_different_path_is_unaffected()
        print("\nALL FILE WRITE SAFETY TESTS PASSED")

    finally:
        # aiosqlite's connection thread is not a daemon, so a failing
        # assertion that skipped the close left the interpreter hanging at
        # exit: the suite never reported its failure, it just stopped, and
        # in CI that is a job running to the time limit instead of a red X.
        await db.close_db()

if __name__ == "__main__":
    asyncio.run(main())
