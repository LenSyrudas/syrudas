"""Agent memory management: the Settings UI's view/add/delete surface."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..tools.memory import MAX_MEMORIES, MAX_MEMORY_CHARS

router = APIRouter(tags=["memories"])


class MemoryIn(BaseModel):
    content: str


@router.get("/memories")
async def list_memories():
    return await db.list_memories()


@router.post("/memories")
async def add_memory(body: MemoryIn):
    content = " ".join(body.content.split())
    if not content:
        raise HTTPException(400, "Memory content is required")
    if len(content) > MAX_MEMORY_CHARS:
        raise HTTPException(400, f"Memory too long (max {MAX_MEMORY_CHARS} characters)")
    try:
        return await db.add_memory(content, cap=MAX_MEMORIES)
    except ValueError:
        raise HTTPException(400, f"Memory is full ({MAX_MEMORIES} entries) - delete some first")


@router.delete("/memories/{mem_id}")
async def delete_memory(mem_id: str):
    if not await db.delete_memory(mem_id):
        raise HTTPException(404, "Memory not found")
    return {"ok": True}


@router.delete("/memories")
async def clear_memories():
    return {"deleted": await db.clear_memories()}
