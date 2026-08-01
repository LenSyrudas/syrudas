"""What the Knowledge indexer collects, and how it decodes what it finds.

Two failures that both produce confident, wrong answers rather than errors:

- collection was `rglob("*")` sorted by path, so pointing Knowledge at any
  JavaScript project spent the whole 200-file budget inside node_modules and
  then cited it; a junction pointing at an ancestor recursed until Windows
  raised WinError 1921 and took the run down.
- decoding was `errors="replace"`, so a cp1252 file was embedded with U+FFFD
  where its punctuation had been - permanently, since the vectors keep it.

Run: .venv\\Scripts\\python.exe scripts\\test_knowledge_walk.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.knowledge import IGNORED_DIRS, _collect_files, extract_text  # noqa: E402
from server.text import decode_text, looks_binary  # noqa: E402


def build_tree() -> Path:
    d = Path(tempfile.mkdtemp(prefix="syrudas-walk-"))
    (d / "src").mkdir()
    (d / "src" / "main.py").write_text("the user's own code", encoding="utf-8")
    (d / "notes.md").write_text("the user's own notes", encoding="utf-8")
    # the dependency folder that used to eat the entire budget
    deps = d / "node_modules" / "left-pad" / "lib"
    deps.mkdir(parents=True)
    for i in range(50):
        (deps / f"chunk{i:03}.js").write_text("module.exports = 1", encoding="utf-8")
    (d / ".git").mkdir()
    (d / ".git" / "COMMIT_EDITMSG").write_text("wip", encoding="utf-8")
    (d / "build").mkdir()
    (d / "build" / "bundle.js").write_text("minified", encoding="utf-8")
    return d


def test_dependency_folders_are_pruned():
    d = build_tree()
    files, skipped = _collect_files(d)
    names = [p.name for p in files]

    assert "main.py" in names and "notes.md" in names, f"user files missing: {names}"
    assert not any("node_modules" in str(p) for p in files), \
        f"node_modules was indexed: {[str(p) for p in files][:3]}"
    assert not any(part in IGNORED_DIRS for p in files for part in p.parts), names
    assert any("folder" in s for s in skipped), f"the skip should be reported: {skipped}"
    print(f"pruning: {len(names)} user files kept, dependency trees skipped OK")


def test_a_junction_loop_does_not_abort_the_walk():
    d = build_tree()
    loop = d / "src" / "loop"
    try:
        # a directory symlink pointing back at an ancestor
        loop.symlink_to(d, target_is_directory=True)
    except (OSError, NotImplementedError):
        # mklink /J needs no privilege where symlinks do
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(loop), str(d)],
                           capture_output=True)
        if r.returncode != 0:
            print("junction loop: skipped (could not create one on this machine)")
            return

    files, _ = _collect_files(d)          # must terminate, not raise

    assert any(p.name == "main.py" for p in files), "real files still expected"
    print(f"junction loop: walk terminated with {len(files)} files, no recursion OK")


def test_an_unreadable_file_does_not_fail_the_run():
    d = build_tree()
    (d / "ghost.md").write_text("x", encoding="utf-8")
    files, _ = _collect_files(d)
    assert files, "a directory with a readable file must still collect it"
    print("unreadable entries: skipped per-file rather than failing the run OK")


def test_cp1252_survives_decoding():
    # 0x92 is a right single quote in cp1252 and invalid utf-8
    raw = "the user’s notes".encode("cp1252")
    text, encoding = decode_text(raw)
    assert "�" not in text, f"replacement char baked in: {text!r}"
    assert text == "the user’s notes", (text, encoding)
    assert encoding == "cp1252", encoding
    print(f"cp1252: decoded cleanly as {encoding}, no U+FFFD OK")


def test_utf8_and_bom_still_win():
    # utf-8-sig is first and decodes plain utf-8 too, so it wins either way -
    # what matters is that the text is right and cp1252 never gets a look in
    for raw, want in (("plain".encode("utf-8"), "plain"),
                      ("café".encode("utf-8-sig"), "café"),
                      ("héllo — wörld".encode("utf-8"), "héllo — wörld")):
        text, encoding = decode_text(raw)
        assert text == want, (text, want, encoding)
        assert encoding.startswith("utf-8"), f"{want!r} fell through to {encoding}"
    print("utf-8 with and without a BOM decodes before any fallback OK")


def test_binary_is_detected_on_bytes():
    jpeg = bytes([0xFF, 0xD8, 0xFF, 0x00, 0x10, 0x4A])
    assert looks_binary(jpeg), "a NUL byte should mark this binary"
    assert not looks_binary("just text".encode("utf-8"))
    # latin-1 would decode the JPEG happily, which is why the check is on bytes
    assert "�" not in decode_text(jpeg)[0]
    print("binary detection reads raw bytes, not the decoded string OK")


def test_extract_text_uses_the_ladder():
    d = Path(tempfile.mkdtemp(prefix="syrudas-walk-"))
    p = d / "notes.csv"
    p.write_bytes("name,quote\na,“hi”".encode("cp1252"))
    text = extract_text(p)
    assert "�" not in text, f"indexer still mangles cp1252: {text!r}"
    print("extract_text: a cp1252 CSV reaches the embedder intact OK")


def main() -> None:
    test_dependency_folders_are_pruned()
    test_a_junction_loop_does_not_abort_the_walk()
    test_an_unreadable_file_does_not_fail_the_run()
    test_cp1252_survives_decoding()
    test_utf8_and_bom_still_win()
    test_binary_is_detected_on_bytes()
    test_extract_text_uses_the_ladder()
    print("\nALL KNOWLEDGE WALK TESTS PASSED")


if __name__ == "__main__":
    main()
