"""Model cookbook: catalog + hardware-aware recommendations, and Ollama pull/
delete. Pulling is Ollama-only; models you pull show up through the normal
provider afterward."""
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import cookbook

router = APIRouter(tags=["cookbook"])


def _ndjson(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


class ModelName(BaseModel):
    name: str


def _valid_name(name: str) -> str:
    name = name.strip()
    if not cookbook.MODEL_NAME_RE.match(name):
        raise HTTPException(400, "Invalid model name")
    return name


@router.get("/cookbook")
async def get_cookbook():
    return await cookbook.build_cookbook()


@router.post("/cookbook/pull")
async def pull(body: ModelName):
    name = _valid_name(body.name)
    base = await cookbook.resolve_ollama_base()
    if not base:
        raise HTTPException(400, "No Ollama detected. Install and run Ollama to download models.")

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for msg in cookbook.ollama_pull(base, name):
                if msg.get("error"):
                    yield _ndjson({"type": "error", "message": str(msg["error"])})
                    return
                total, completed = msg.get("total"), msg.get("completed")
                percent = (
                    round(completed / total * 100, 1)
                    if isinstance(total, (int, float)) and total and isinstance(completed, (int, float))
                    else None
                )
                yield _ndjson({
                    "type": "progress",
                    "status": msg.get("status", ""),
                    "percent": percent,
                    "completed": completed,
                    "total": total,
                })
                if msg.get("status") == "success":
                    yield _ndjson({"type": "done"})
                    return
            yield _ndjson({"type": "done"})
        except Exception as exc:
            yield _ndjson({"type": "error", "message": f"{exc}"})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.post("/cookbook/delete")
async def delete(body: ModelName):
    name = _valid_name(body.name)
    base = await cookbook.resolve_ollama_base()
    if not base:
        raise HTTPException(400, "No Ollama detected.")
    try:
        await cookbook.ollama_delete(base, name)
    except Exception as exc:
        raise HTTPException(502, f"Could not remove model: {exc}")
    return {"ok": True}
