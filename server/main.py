"""Argos - self-hosted AI workspace with pluggable model providers."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import WEB_DIST
from .routes import api_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_db()
    yield
    from .mcp_client import close_all
    await close_all()
    await db.close_db()


app = FastAPI(title="Argos", lifespan=lifespan)
app.include_router(api_router)


@app.get("/api/health")
async def health():
    return {"ok": True, "app": "argos"}


if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        file = WEB_DIST / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(WEB_DIST / "index.html")
