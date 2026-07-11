from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agent import resolve_approval

router = APIRouter(tags=["approvals"])


class ApprovalIn(BaseModel):
    approve: bool


@router.post("/approvals/{approval_id}")
async def approve(approval_id: str, body: ApprovalIn):
    if not resolve_approval(approval_id, body.approve):
        raise HTTPException(404, "No pending approval with that id")
    return {"ok": True}
