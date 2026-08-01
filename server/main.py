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

def _setup_logging() -> None:
    """One log artifact, the same from source and from the packaged exe.

    basicConfig had no timestamps, so a run line could not be placed in time,
    and the file only existed in the windowed build - where stdout happens to
    be redirected - despite the setup guide promising data\\syrudas.log either
    way. Rotating, because an agent that runs tools writes a line per step.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        from logging.handlers import RotatingFileHandler
        from .config import DATA_DIR
        f = RotatingFileHandler(DATA_DIR / "syrudas.log", maxBytes=2_000_000,
                                backupCount=3, encoding="utf-8")
        f.setFormatter(fmt)
        root.addHandler(f)
    except OSError:
        # an unwritable data folder is reported properly by ensure_dirs;
        # losing the file log must not stop the app starting
        root.warning("Could not open the log file; logging to the console only")


_setup_logging()


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
