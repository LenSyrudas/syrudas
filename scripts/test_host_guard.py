"""Tests for the localhost-only Host-header guard (DNS-rebinding defense).

Covers the pure allowlist logic and an end-to-end pass through the real ASGI
app: loopback Hosts reach the route, external Hosts get a 403 before it.

Run: .venv\\Scripts\\python.exe scripts\\test_host_guard.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# keep the guard test off the user's real database
from server import db  # noqa: E402
db.DB_PATH = Path(tempfile.mkdtemp(prefix="syrudas-hostguard-")) / "test.db"

from starlette.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402
from server.security import is_local_host  # noqa: E402


def test_allowlist_logic():
    allowed = [
        "127.0.0.1:8040", "127.0.0.1", "localhost", "localhost:8040",
        "LocalHost:8040", "[::1]:8040", "[::1]", "127.5.6.7",  # 127/8 is loopback
        None, "",  # missing Host: non-browser local clients
    ]
    blocked = [
        "evil.com", "evil.com:8040", "syrudas.attacker.test",
        "127.0.0.1.evil.com", "192.168.1.50:8040", "10.0.0.1",
        "169.254.169.254", "example.com", "0.0.0.0",
    ]
    for h in allowed:
        assert is_local_host(h), f"should ALLOW {h!r}"
    for h in blocked:
        assert not is_local_host(h), f"should BLOCK {h!r}"
    print(f"allowlist: {len(allowed)} loopback/absent allowed, "
          f"{len(blocked)} external blocked OK")


def test_app_enforces_guard():
    client = TestClient(app)

    r = client.get("/api/health", headers={"Host": "127.0.0.1:8040"})
    assert r.status_code == 200, r.status_code
    assert r.json().get("ok") is True

    r = client.get("/api/health", headers={"Host": "localhost"})
    assert r.status_code == 200, r.status_code

    # a DNS-rebinding drive-by: attacker domain resolved to 127.0.0.1, so the
    # request reaches the socket, but its Host header gives it away
    r = client.get("/api/health", headers={"Host": "evil.attacker.test"})
    assert r.status_code == 403, r.status_code
    assert "local" in r.text.lower()

    # the credit-burning vector must be blocked too, not just health
    r = client.post("/v1/chat/completions", headers={"Host": "evil.attacker.test"},
                    json={"model": "x", "messages": []})
    assert r.status_code == 403, r.status_code
    print("app: loopback Hosts reach routes, external Hosts 403 before them OK")


def main():
    test_allowlist_logic()
    test_app_enforces_guard()
    print("\nALL HOST-GUARD TESTS PASSED")


if __name__ == "__main__":
    main()
