"""Tests for the model cookbook: catalog integrity, hardware-aware fit ratings,
the Ollama client (via httpx.MockTransport - no daemon), and the routes.

Run: .venv\\Scripts\\python.exe scripts\\test_cookbook.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-cookbook-"))
from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

import httpx  # noqa: E402

from server import cookbook  # noqa: E402
from server.cookbook import CATALOG, rate_fit  # noqa: E402


def hw(*, gpus=None, ram_gb=16):
    return {
        "os": "Windows",
        "cpu": {"name": "Test CPU", "cores": 8, "threads": 16},
        "ram": {"total_mb": int(ram_gb * 1024), "available_mb": int(ram_gb * 512)},
        "gpus": gpus or [],
        "notes": [],
    }


def gpu(vram_gb, *, estimated=False, capped=False):
    return {"name": "GPU", "vendor": "NVIDIA", "vram_total_mb": int(vram_gb * 1024),
            "vram_free_mb": None, "vram_estimated": estimated, "vram_capped": capped}


def test_catalog_integrity():
    names = [e["name"] for e in CATALOG]
    assert len(names) == len(set(names)), "duplicate model names"
    for e in CATALOG:
        assert cookbook.MODEL_NAME_RE.match(e["name"]), e["name"]
        assert {"params", "size_gb", "min_vram_gb", "min_ram_gb", "tags", "blurb"} <= e.keys()
        assert e["tags"] and all(isinstance(t, str) for t in e["tags"])
    assert any("embedding" in e["tags"] for e in CATALOG), "need an embedding model for RAG"
    assert any("tools" in e["tags"] for e in CATALOG), "need a tool-capable model for agent mode"
    print(f"catalog: {len(CATALOG)} models, unique names, valid ids, tags present OK")


def test_fit_ratings():
    small = next(e for e in CATALOG if e["name"] == "llama3.2:3b")   # 4 GB vram, 6 GB ram
    big = next(e for e in CATALOG if e["name"] == "qwen2.5:32b")     # 24 GB vram, 40 GB ram

    assert rate_fit(hw(gpus=[gpu(24)]), small)[0] == "good"
    assert rate_fit(hw(gpus=[gpu(4)]), small)[0] == "tight"          # 4 <= 4, > 4*0.9
    # too big for a small GPU but ample RAM -> CPU offload
    assert rate_fit(hw(gpus=[gpu(6)], ram_gb=64), big)[0] == "cpu"
    # no GPU, plenty of RAM -> runs on CPU
    assert rate_fit(hw(ram_gb=64), small)[0] == "cpu"
    # no GPU, not enough RAM -> too big
    assert rate_fit(hw(ram_gb=8), big)[0] == "too_big"
    # estimated/capped VRAM that's smaller than needed -> unknown, not a false "too big"
    assert rate_fit(hw(gpus=[gpu(4, capped=True)], ram_gb=8), big)[0] == "unknown"
    # no hardware info at all -> unknown
    assert rate_fit({"gpus": [], "ram": {"total_mb": None}}, small)[0] == "unknown"
    print("fit ratings: good/tight/cpu/too_big/unknown across GPU+RAM combos OK")


def _mock_ollama(handler):
    """Patch httpx.AsyncClient so cookbook's Ollama calls hit a MockTransport."""
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, *a, **k):
            k["transport"] = transport
            super().__init__(*a, **k)

    httpx.AsyncClient = Patched
    return real


async def test_ollama_client():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.0"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"},
                                                        {"name": "nomic-embed-text:latest"}]})
        if path == "/api/pull":
            body = (b'{"status":"pulling manifest"}\n'
                    b'{"status":"downloading","total":100,"completed":40}\n'
                    b'{"status":"success"}\n')
            return httpx.Response(200, content=body)
        return httpx.Response(404)

    real = _mock_ollama(handler)
    try:
        base = await cookbook.resolve_ollama_base()
        assert base and base.endswith(":11434"), base
        installed = await cookbook.ollama_installed(base)
        assert "llama3.1:8b" in installed
        # installed-match handles both exact tags and bare (":latest") names
        assert cookbook._installed_match("llama3.1:8b", installed)
        assert cookbook._installed_match("nomic-embed-text", installed)
        assert not cookbook._installed_match("mistral:7b", installed)
        # a bare entry must match ONLY :latest, not some other tag (else Remove
        # would target a :latest that isn't installed and 404)
        assert cookbook._installed_match("nomic-embed-text", ["nomic-embed-text:latest"])
        assert not cookbook._installed_match("nomic-embed-text", ["nomic-embed-text:v1.5"])
        msgs = [m async for m in cookbook.ollama_pull(base, "llama3.2:1b")]
        assert msgs[-1]["status"] == "success" and any(m.get("total") for m in msgs)
    finally:
        httpx.AsyncClient = real
    print("ollama client: version probe, tags, install-match, pull stream OK")


async def test_resolve_prefers_configured_provider():
    # a provider on a custom Ollama host should be discovered and /v1 stripped
    await db.create_provider_instance("openai_compat", "Ollama", {"base_url": "http://box:9999/v1"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "box" and request.url.port == 9999:
            return httpx.Response(200, json={"version": "1"})
        return httpx.Response(404)

    real = _mock_ollama(handler)
    try:
        base = await cookbook.resolve_ollama_base()
        assert base == "http://box:9999", base
    finally:
        httpx.AsyncClient = real
        await db.clear_arena()  # touch db to keep connection warm; noop cleanup
    print("resolve: derives Ollama root from a configured provider (/v1 stripped) OK")


def test_routes():
    from starlette.testclient import TestClient

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.0"})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2:1b"}]})
        if path == "/api/pull":
            return httpx.Response(200, content=(
                b'{"status":"downloading","total":10,"completed":10}\n{"status":"success"}\n'))
        if path == "/api/delete":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    real = _mock_ollama(handler)
    try:
        from server.main import app
        client = TestClient(app)
        local = {"Host": "127.0.0.1:8040"}

        book = client.get("/api/cookbook", headers=local).json()
        assert book["ollama"]["configured"] is True
        assert len(book["catalog"]) == len(CATALOG)
        assert all("fit" in m and "installed" in m for m in book["catalog"])
        assert next(m for m in book["catalog"] if m["name"] == "llama3.2:1b")["installed"] is True

        # invalid name rejected
        assert client.post("/api/cookbook/pull", headers=local,
                           json={"name": "bad name!!"}).status_code == 400

        with client.stream("POST", "/api/cookbook/pull", headers=local,
                           json={"name": "llama3.2:3b"}) as r:
            assert r.status_code == 200
            text = "".join(r.iter_text())
        assert '"progress"' in text and '"percent": 100' in text and '"done"' in text

        assert client.post("/api/cookbook/delete", headers=local,
                          json={"name": "llama3.2:1b"}).json()["ok"] is True
    finally:
        httpx.AsyncClient = real
    print("routes: /cookbook assembly, name validation, pull stream, delete OK")


def test_route_error_paths():
    from starlette.testclient import TestClient

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.1.0"})
        if path == "/api/pull":
            # Ollama reports a failure mid-stream as an {"error": ...} line
            return httpx.Response(200, content=b'{"error":"model not found"}\n')
        if path == "/api/delete":
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(404)

    real = _mock_ollama(handler)
    try:
        from server.main import app
        client = TestClient(app)
        local = {"Host": "127.0.0.1:8040"}

        with client.stream("POST", "/api/cookbook/pull", headers=local,
                           json={"name": "ghost:1b"}) as r:
            assert r.status_code == 200
            text = "".join(r.iter_text())
        assert '"error"' in text and "model not found" in text

        # delete of a model Ollama rejects -> 502 (not a 500)
        r = client.post("/api/cookbook/delete", headers=local, json={"name": "ghost:1b"})
        assert r.status_code == 502, r.status_code
    finally:
        httpx.AsyncClient = real
    print("routes: pull error-event surfaced, delete failure -> 502 OK")


async def test_no_ollama_paths():
    async def none():
        return None
    real_resolve = cookbook.resolve_ollama_base
    cookbook.resolve_ollama_base = none
    try:
        book = await cookbook.build_cookbook()
        assert book["ollama"]["configured"] is False
        assert book["installed"] == []
        # recommendations still present without Ollama
        assert len(book["catalog"]) == len(CATALOG) and all("fit" in m for m in book["catalog"])

        from starlette.testclient import TestClient
        from server.main import app
        client = TestClient(app)
        local = {"Host": "127.0.0.1:8040"}
        r = client.post("/api/cookbook/pull", headers=local, json={"name": "llama3.2:1b"})
        assert r.status_code == 400 and "Ollama" in r.json()["detail"]
    finally:
        cookbook.resolve_ollama_base = real_resolve
    print("no-ollama: recommendations still built, pull refused with a clear 400 OK")


async def main():
    test_catalog_integrity()
    test_fit_ratings()
    await test_ollama_client()
    await test_resolve_prefers_configured_provider()
    await test_no_ollama_paths()
    await db.close_db()
    test_routes()
    test_route_error_paths()
    print("\nALL COOKBOOK TESTS PASSED")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
