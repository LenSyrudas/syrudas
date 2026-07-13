"""Writing editor: document CRUD plus a stateless streaming AI-edit endpoint.

The edit endpoint applies an instruction to a selected span (or the whole
document / an insertion point) and streams back ONLY the replacement text,
so the frontend can preview it and accept/reject into the document.
"""
import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import db
from ..providers.registry import create_provider
from ..schemas import GenParams, Message

router = APIRouter(tags=["documents"])

MAX_CONTEXT_CHARS = 12000  # cap the surrounding-doc context sent to the model


def _ndjson(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


class DocumentIn(BaseModel):
    title: str = "Untitled"
    content: str = ""


class DocumentPatch(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


@router.get("/documents")
async def list_documents():
    return await db.list_documents()


@router.post("/documents")
async def create_document(body: DocumentIn):
    return await db.create_document(body.title, body.content)


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    doc = await db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@router.put("/documents/{doc_id}")
async def update_document(doc_id: str, body: DocumentPatch):
    fields = body.model_dump(exclude_none=True)
    doc = await db.update_document(doc_id, **fields)
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    if not await db.delete_document(doc_id):
        raise HTTPException(404, "Document not found")
    return {"ok": True}


class EditRequest(BaseModel):
    provider_id: str
    model: str
    instruction: str
    selection: str = ""      # text to revise; empty = insert/continue
    context: str = ""        # surrounding document, for grounding
    params: Optional[GenParams] = None


EDIT_SYSTEM = (
    "You are a precise writing assistant embedded in a text editor. Apply the "
    "user's instruction and return ONLY the text that should be inserted into "
    "the document - no preamble, no explanation, no surrounding quotation marks "
    "or code fences. Match the document's existing voice and formatting. When "
    "revising a selection, return the full revised replacement for it. When there "
    "is no selection, return only the new text to insert at the cursor."
)


@router.post("/documents/edit")
async def edit(body: EditRequest):
    """Stateless streaming AI edit - no document is created or modified here;
    the client decides whether to accept the streamed replacement."""
    inst = await db.get_provider_instance(body.provider_id)
    if not inst:
        raise HTTPException(400, "Unknown provider instance")
    if not body.instruction.strip():
        raise HTTPException(400, "An instruction is required")
    provider = create_provider(inst["type_id"], inst["config"])

    parts = []
    context = body.context.strip()
    if context:
        parts.append(f"Document (for context):\n{context[:MAX_CONTEXT_CHARS]}")
    if body.selection.strip():
        parts.append(f"Selected text to revise:\n{body.selection}")
    else:
        parts.append("There is no selection; produce text to insert at the cursor.")
    parts.append(f"Instruction: {body.instruction.strip()}")
    messages = [
        Message(role="system", content=EDIT_SYSTEM),
        Message(role="user", content="\n\n".join(parts)),
    ]

    async def event_stream() -> AsyncIterator[str]:
        got_done = False
        try:
            async for ev in provider.chat(body.model, messages, params=body.params):
                if ev.type == "done":
                    got_done = True
                yield _ndjson(ev.model_dump(exclude_none=True))
        except Exception as exc:
            yield _ndjson({"type": "error", "message": f"{exc}"})
        if not got_done:
            yield _ndjson({"type": "done"})

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
