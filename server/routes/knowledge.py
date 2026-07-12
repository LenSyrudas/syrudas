"""Knowledge (local RAG) management: embedding config, indexing, search."""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db, knowledge

router = APIRouter(tags=["knowledge"])


class EmbeddingIn(BaseModel):
    provider_id: str
    model: str


class IndexIn(BaseModel):
    path: str


class SearchIn(BaseModel):
    query: str


async def _embedding_config() -> dict | None:
    raw = await db.get_setting(knowledge.EMBEDDING_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


@router.get("/knowledge")
async def get_knowledge():
    return {
        "embedding": await _embedding_config(),
        "sources": await db.list_knowledge_sources(),
        "chunks": await db.count_knowledge_chunks(),
    }


@router.put("/knowledge/embedding")
async def set_embedding(body: EmbeddingIn):
    try:
        dim = await knowledge.probe(body.provider_id, body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"Embedding test failed: {exc}")
    old = await _embedding_config()
    changed = old and (old.get("provider_id") != body.provider_id
                       or old.get("model") != body.model)
    cleared = 0
    if changed:
        # embeddings from different models are not comparable - a stale index
        # would silently return garbage matches
        cleared = await db.clear_knowledge()
    await db.set_setting(knowledge.EMBEDDING_KEY, json.dumps(
        {"provider_id": body.provider_id, "model": body.model}))
    return {"ok": True, "dim": dim, "cleared_sources": cleared}


@router.post("/knowledge/index")
async def index(body: IndexIn):
    path = body.path.strip()
    if not path:
        raise HTTPException(400, "Path is required")
    try:
        return await knowledge.index_path(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Indexing failed: {exc}")


@router.post("/knowledge/search")
async def search(body: SearchIn):
    query = body.query.strip()
    if not query:
        raise HTTPException(400, "Query is required")
    try:
        return {"results": await knowledge.search(query)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Search failed: {exc}")


@router.delete("/knowledge/sources/{source_id}")
async def delete_source(source_id: str):
    if not await db.delete_knowledge_source(source_id):
        raise HTTPException(404, "Source not found")
    return {"ok": True}


@router.delete("/knowledge")
async def clear():
    return {"deleted": await db.clear_knowledge()}
