import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from .. import db, runs

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
    if not await runs.wait_idle(conv_id):
        raise HTTPException(
            409, "A response is still being generated for this conversation - stop it first.")
    runs.bump_generation(conv_id)
    await db.delete_conversation(conv_id)
    return {"ok": True}


class RewindIn(BaseModel):
    # regenerate: drop everything AFTER the last user message (keep it)
    # edit: drop the last user message too and hand its content back
    include_last_user: bool = False


@router.post("/conversations/{conv_id}/rewind")
async def rewind_conversation(conv_id: str, body: RewindIn):
    if not await db.get_conversation(conv_id):
        raise HTTPException(404, "Conversation not found")
    # never rewrite history under a stream that is still persisting messages
    if not await runs.wait_idle(conv_id):
        raise HTTPException(
            409, "A response is still being generated for this conversation - stop it first.")
    last_user = await db.get_last_user_message(conv_id)
    if not last_user:
        raise HTTPException(400, "Conversation has no user message to rewind to")
    await db.delete_messages_from(conv_id, last_user["id"],
                                  inclusive=body.include_last_user)
    runs.bump_generation(conv_id)
    return {
        "ok": True,
        "removed_user_content": last_user["content"] if body.include_last_user else None,
    }


@router.get("/conversations/{conv_id}/export")
async def export_conversation(conv_id: str):
    conv = await db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "Conversation not found")
    messages = await db.list_messages(conv_id)

    lines = [
        f"# {conv['title']}",
        "",
        f"*Exported from Syrudas AI · model `{conv['model']}` · created {conv['created_at'][:10]}*",
        "",
    ]
    role_names = {"user": "You", "assistant": "Assistant", "tool": "Tool result",
                  "system": "System"}
    for m in messages:
        lines.append(f"## {role_names.get(m['role'], m['role'])}")
        lines.append("")
        if m["content"]:
            lines.append(m["content"])
            lines.append("")
        for tc in m["tool_calls"] or []:
            lines.append(f"**Tool call:** `{tc['name']}`")
            lines.append("```json")
            lines.append(json.dumps(tc.get("arguments", {}), indent=2))
            lines.append("```")
            lines.append("")

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", conv["title"]).strip("-.")[:60] or "conversation"
    return Response(
        content="\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}.md"'},
    )
