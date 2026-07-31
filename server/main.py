"""Syrudas AI - self-hosted AI workspace with pluggable model providers."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import APP_VERSION, WEB_DIST
from .onboarding import auto_detect_providers
from .routes import api_router
from .routes.openai_api import router as openai_router
from .security import LocalhostOnlyMiddleware

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_db()
    await auto_detect_providers()
    yield
    from .agent import drain_detached_writes
    from .mcp_client import close_all
    await close_all()
    # a run cancelled on the way down still owes its tool_calls an answer
    await drain_detached_writes()
    await db.close_db()


app = FastAPI(title="Syrudas AI", version=APP_VERSION, lifespan=lifespan)
# reject requests carrying a non-loopback Host header (DNS-rebinding defense)
app.add_middleware(LocalhostOnlyMiddleware)
app.include_router(api_router)
# OpenAI-compatible surface: external tools use Syrudas as a model hub
app.include_router(openai_router)


@app.get("/api/health")
async def health():
    return {"ok": True, "app": "syrudas", "version": APP_VERSION}


if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")
    WEB_ROOT = WEB_DIST.resolve()

    @app.get("/{path:path}")
    async def spa(path: str):
        """Serve a bundled asset, falling back to index.html for client routes.

        The resolved path must stay inside the bundle. Without that check there
        are two ways out, both reachable: on Windows `WEB_DIST / "C:/..."`
        discards the base entirely when the right-hand side is drive-absolute,
        and a percent-encoded traversal survives the path normalization the
        server does before this handler sees it. Either one served any readable
        file - including data/syrudas.db, which holds provider API keys in
        plaintext - and served it from this application's own origin, so any
        HTML reachable on disk would run with same-origin access to /api and
        /v1. resolve() also collapses symlinks, so a link planted inside the
        bundle cannot point out of it.
        """
        index = WEB_ROOT / "index.html"
        if not path:
            return FileResponse(index)
        target = (WEB_ROOT / path).resolve()
        if target.is_relative_to(WEB_ROOT) and target.is_file():
            return FileResponse(target)
        return FileResponse(index)
