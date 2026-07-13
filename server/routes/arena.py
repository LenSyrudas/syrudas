"""Blind arena: record votes from side-by-side model comparisons and expose
a per-model leaderboard. The comparison itself runs in the browser via two
/api/complete calls; this only tallies the outcomes."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

router = APIRouter(tags=["arena"])

VALID_WINNERS = {"a", "b", "tie", "both_bad"}


class VoteIn(BaseModel):
    model_a: str
    model_b: str
    winner: str


@router.post("/arena/vote")
async def vote(body: VoteIn):
    if body.winner not in VALID_WINNERS:
        raise HTTPException(400, f"winner must be one of {sorted(VALID_WINNERS)}")
    a, b = body.model_a.strip(), body.model_b.strip()
    if not a or not b:
        raise HTTPException(400, "both model labels are required")
    if a == b:
        raise HTTPException(400, "cannot compare a model against itself")
    await db.add_arena_result(a, b, body.winner)
    return {"ok": True}


@router.get("/arena/leaderboard")
async def leaderboard():
    return await db.arena_leaderboard()


@router.delete("/arena/leaderboard")
async def reset():
    return {"deleted": await db.clear_arena()}
