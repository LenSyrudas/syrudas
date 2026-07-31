"""The SPA fallback route must never serve a file outside the web bundle.

Drives the REAL app (server/main.py) over a REAL HTTP server. That matters: an
in-process test client normalizes the URL before the request is built, which
hides the percent-encoded traversal entirely - the exact form that survives the
server's own path normalization and reaches the handler intact. Tested through
the convenient boundary, this suite would pass over a live hole.

The server runs in a CHILD PROCESS, not a background thread, so teardown is a
kill rather than a hope. An earlier version used a daemon thread; it exited
cleanly on a developer machine and hung a CI runner for the full twenty-minute
job limit. A test that can hang the suite is worse than the bug it guards.

Everything runs against a temporary bundle and a temporary database, so the
"secrets" below are throwaway files, never the user's real data folder.

Covers:
- ordinary pages and real assets are still served
- unknown client routes still fall back to index.html (SPA behaviour intact)
- a drive-absolute request path cannot escape (pathlib discards the base when
  the right-hand side has a drive, so a naive prefix test would not catch it)
- a percent-encoded traversal cannot escape
- a plain ../ traversal cannot escape
- a symlink planted inside the bundle cannot point out of it

Run: .venv\\Scripts\\python.exe scripts\\test_spa_paths.py
"""
import logging
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request otherwise

ROOT = Path(__file__).resolve().parent.parent

TMP = Path(tempfile.mkdtemp(prefix="syrudas-spa-"))
DIST = TMP / "web" / "dist"
(DIST / "assets").mkdir(parents=True)
(DIST / "index.html").write_text("<html>THE APP</html>", encoding="utf-8")
(DIST / "assets" / "app.js").write_text("console.log('asset')", encoding="utf-8")

# stand-ins for the real data folder, which sits two levels up from web/dist
SECRET = TMP / "data" / "syrudas.db"
SECRET.parent.mkdir()
SECRET.write_text("SQLITE-WITH-PLAINTEXT-API-KEYS", encoding="utf-8")

STARTUP_TIMEOUT_S = 60
SHUTDOWN_TIMEOUT_S = 10

# The child patches config before importing server.main, so `from .config import
# WEB_DIST` there picks up the temporary bundle. auto-detection is stubbed out so
# the test never probes for the developer's real model backends.
BOOT = f'''
import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})

from server import config
config.WEB_DIST = Path({str(DIST)!r})
config.DB_PATH = Path({str(TMP / "test.db")!r})

from server import db
db.DB_PATH = Path({str(TMP / "test.db")!r})

from server import main as m

async def _no_autodetect():
    return []

m.auto_detect_providers = _no_autodetect

import uvicorn
uvicorn.run(m.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="critical")
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"


def serve() -> subprocess.Popen:
    boot = TMP / "boot_server.py"
    boot.write_text(BOOT, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(boot), str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    deadline = time.time() + STARTUP_TIMEOUT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            err = (proc.stderr.read() or b"").decode("utf-8", "replace")[-2000:]
            raise RuntimeError(f"test server exited during startup:\n{err}")
        try:
            httpx.get(BASE + "/", timeout=2)
            return proc
        except Exception:
            time.sleep(0.1)
    stop(proc)
    raise RuntimeError(f"test server did not answer within {STARTUP_TIMEOUT_S}s")


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=SHUTDOWN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=SHUTDOWN_TIMEOUT_S)


def get(url_path: str) -> httpx.Response:
    # the URL is passed through untouched; httpx must not re-encode %2f
    return httpx.get(BASE + url_path, timeout=15)


def assert_app(url_path: str, why: str) -> None:
    body = get(url_path).text
    assert "SQLITE-WITH-PLAINTEXT-API-KEYS" not in body, f"LEAKED the database via {url_path}"
    assert "16-bit app support" not in body, f"LEAKED a system file via {url_path}"
    assert "THE APP" in body, f"{why}: expected the app shell, got {body[:80]!r}"


# --- tests ---

def test_normal_serving():
    assert "THE APP" in get("/").text
    assert "console.log" in get("/assets/app.js").text, "real assets must still be served"
    assert_app("/settings", "an unknown client route must fall back to index.html")
    print("normal serving: pages, assets and client routes OK")


def test_drive_absolute_is_contained():
    # pathlib discards the left operand when the right one carries a drive, so
    # `WEB_DIST / "C:/Windows/win.ini"` is simply C:\Windows\win.ini
    assert_app("/C:/Windows/win.ini", "a drive-absolute path must not escape")
    leaked = str(SECRET).replace("\\", "/")
    assert_app("/" + leaked, "a drive-absolute path to the database must not escape")
    print("drive-absolute request path: contained OK")


def test_traversal_is_contained():
    assert_app("/../../data/syrudas.db", "a plain ../ traversal must not escape")
    assert_app("/..%2f..%2fdata%2fsyrudas.db", "an encoded traversal must not escape")
    assert_app("/%2e%2e/%2e%2e/data/syrudas.db", "an encoded dot-dot must not escape")
    print("traversal, plain and percent-encoded: contained OK")


def test_symlink_is_contained():
    link = DIST / "escape.db"
    try:
        link.symlink_to(SECRET)
    except (OSError, NotImplementedError):
        print("symlink escape: skipped (needs privilege on this machine)")
        return
    try:
        assert_app("/escape.db", "a symlink out of the bundle must not be followed")
        print("symlink out of the bundle: contained OK")
    finally:
        link.unlink()


def main() -> None:
    proc = serve()
    try:
        test_normal_serving()
        test_drive_absolute_is_contained()
        test_traversal_is_contained()
        test_symlink_is_contained()
    finally:
        stop(proc)
    print("\nALL SPA PATH TESTS PASSED")


if __name__ == "__main__":
    main()
