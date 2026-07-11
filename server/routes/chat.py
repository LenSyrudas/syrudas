import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import db
from ..chat import stream_plain_chat, title_from
from ..providers.registry import create_provider
from ..schemas import GenParams

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    provider_id: str
    model: str
    message: str
    agent_mode: bool = False
    system_prompt: str = ""
    params: Optional[GenParams] = None


def _ndjson(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


@router.post("/chat")
async def chat(req: ChatRequest):
    inst = await db.get_provider_instance(req.provider_id)
    if not inst:
        raise HTTPException(400, "Unknown provider instance")

    if req.conversation_id:
        conv = await db.get_conversation(req.conversation_id)
        if not conv:
            raise HTTPException(404, "Conversation not found")
        await db.update_conversation(
            conv["id"], provider_id=req.provider_id, model=req.model,
            agent_mode=int(req.agent_mode),
        )
        conv = await db.get_conversation(conv["id"])
    else:
        conv = await db.create_conversation(
            req.provider_id, req.model, req.agent_mode, req.system_prompt)
        await db.update_conversation(conv["id"], title=title_from(req.message))
        conv = await db.get_conversation(conv["id"])

    await db.add_message(conv["id"], "user", req.message)
    provider = create_provider(inst["type_id"], inst["config"])

    async def event_stream() -> AsyncIterator[str]:
        yield _ndjson({"type": "meta", "conversation_id": conv["id"], "title": conv["title"]})
        try:
            if conv["agent_mode"]:
                from ..agent import stream_agent_chat
                gen = stream_agent_chat(conv, provider, params=req.params)
            else:
                gen = stream_plain_chat(conv, provider, params=req.params)
            async for event in gen:
                yield _ndjson(event)
        except Exception as exc:  # surface unexpected failures to the UI
            yield _ndjson({"type": "error", "message": f"Server error: {exc}"})
            yield _ndjson({"type": "done"})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
