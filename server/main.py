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

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_db()
    await auto_detect_providers()
    yield
    from .mcp_client import close_all
    await close_all()
    await db.close_db()


app = FastAPI(title="Syrudas AI", version=APP_VERSION, lifespan=lifespan)
app.include_router(api_router)
# OpenAI-compatible surface: external tools use Syrudas as a model hub
app.include_router(openai_router)


@app.get("/api/health")
async def health():
    return {"ok": True, "app": "syrudas", "version": APP_VERSION}


if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        file = WEB_DIST / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(WEB_DIST / "index.html")
