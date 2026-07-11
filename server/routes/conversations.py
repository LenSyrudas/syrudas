from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

router = APIRouter(tags=["conversations"])


class ConversationPatch(BaseModel):
    title: str | None = None
    provider_id: str | None = None
    model: str | None = None
    agent_mode: bool | None = None
    system_prompt: str | None = None


@router.get("/conversations")
async def list_conversations():
    return await db.list_conversations()


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    conv["messages"] = await db.list_messages(conv_id)
    return conv


@router.patch("/conversations/{conv_id}")
async def patch_conversation(conv_id: str, patch: ConversationPatch):
    if not await db.get_conversation(conv_id):
        raise HTTPException(404, "Conversation not found")
    fields = {k: v for k, v in patch.model_dump(exclude_none=True).items()}
    if "agent_mode" in fields:
        fields["agent_mode"] = int(fields["agent_mode"])
    await db.update_conversation(conv_id, **fields)
    return await db.get_conversation(conv_id)


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    await db.delete_conversation(conv_id)
    return {"ok": True}
