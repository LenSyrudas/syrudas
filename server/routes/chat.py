import json
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import db
from ..chat import stream_plain_chat, title_from
from ..providers.registry import create_provider
from ..schemas import GenParams

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    provider_id: str
    model: str
    # empty message + existing conversation = continue from history
    message: str = ""
    agent_mode: bool = False
    # applied on conversation creation only; PATCH /conversations/{id} edits it
    # later (sending it on every message would let a stale client clobber it)
    system_prompt: str = ""
    # drop everything after the last user message before responding; rolled
    # back if the provider fails before producing anything
    regenerate: bool = False
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
        if not req.message:
            raise HTTPException(400, "A new conversation needs a message")
        conv = await db.create_conversation(
            req.provider_id, req.model, req.agent_mode, req.system_prompt)
        await db.update_conversation(conv["id"], title=title_from(req.message))
        conv = await db.get_conversation(conv["id"])

    # regenerate: capture what we delete so a provider that fails before
    # producing anything doesn't cost the user their previous reply
    removed_rows: list[dict] = []
    if req.regenerate and req.conversation_id:
        last_user = await db.get_last_user_message(conv["id"])
        if last_user:
            removed_rows = await db.get_messages_after(conv["id"], last_user["id"])
            if removed_rows:
                await db.delete_messages_from(conv["id"], removed_rows[0]["id"],
                                              inclusive=True)

    if req.message:
        # editing/resending into a conversation whose user turns were all
        # removed re-titles it, so the sidebar doesn't show deleted text
        had_user = await db.get_last_user_message(conv["id"]) is not None
        await db.add_message(conv["id"], "user", req.message)
        if not had_user and req.conversation_id:
            await db.update_conversation(conv["id"], title=title_from(req.message))
            conv = await db.get_conversation(conv["id"])
    elif not await db.get_last_user_message(conv["id"]):
        raise HTTPException(400, "Nothing to continue: the conversation has no user message")
    provider = create_provider(inst["type_id"], inst["config"])

    async def event_stream() -> AsyncIterator[str]:
        yield _ndjson({"type": "meta", "conversation_id": conv["id"], "title": conv["title"]})
        got_content = False
        try:
            if conv["agent_mode"]:
                from ..agent import stream_agent_chat
                gen = stream_agent_chat(conv, provider, params=req.params)
            else:
                gen = stream_plain_chat(conv, provider, params=req.params)
            async for event in gen:
                if event.get("type") in ("text_delta", "tool_call"):
                    got_content = True
                yield _ndjson(event)
        except Exception as exc:  # surface unexpected failures to the UI
            yield _ndjson({"type": "error", "message": f"Server error: {exc}"})
            yield _ndjson({"type": "done"})
        # (not reached on client disconnect - acceptable: the reply is only
        # lost if the user aborts before the first token)
        if removed_rows and not got_content:
            log.info("Regenerate produced nothing; restoring %d messages", len(removed_rows))
            await db.restore_messages(removed_rows)

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
