"""Pause/resume an active agent run.

Pause is cooperative: it sets a flag the agent loop picks up at the next step
boundary, where parking is safe (mid-token, the stream cannot stop cleanly).
Resume unparks a run that actually parked; anything else is a 409, not a
silent no-op the UI would have to guess about.
"""
from fastapi import APIRouter, HTTPException

from .. import runs
from ..agent import request_pause, request_resume

router = APIRouter(tags=["runs"])


@router.post("/runs/{conv_id}/pause")
async def pause_run(conv_id: str):
    if not runs.is_active(conv_id):
        raise HTTPException(409, "No active run for this conversation")
    request_pause(conv_id)
    return {"ok": True}


@router.post("/runs/{conv_id}/resume")
async def resume_run(conv_id: str):
    if not request_resume(conv_id):
        raise HTTPException(409, "This conversation has no paused run")
    return {"ok": True}
