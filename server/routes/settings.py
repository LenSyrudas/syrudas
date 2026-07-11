"""User settings endpoints (currently: agent file-access folders)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import db
from ..config import DEFAULT_WORKSPACE

router = APIRouter(tags=["settings"])

AGENT_FOLDERS_KEY = "agent_folders"


async def get_agent_folders() -> list[str]:
    try:
        folders = json.loads(await db.get_setting(AGENT_FOLDERS_KEY, "[]"))
    except json.JSONDecodeError:
        return []
    return [f for f in folders if isinstance(f, str)]


class AgentFoldersIn(BaseModel):
    folders: list[str] = Field(default_factory=list)


@router.get("/settings/agent-folders")
async def read_agent_folders():
    folders = await get_agent_folders()
    return {
        "workspace": str(DEFAULT_WORKSPACE),
        "folders": folders,
        "missing": [f for f in folders if not Path(f).is_dir()],
    }


@router.put("/settings/agent-folders")
async def write_agent_folders(body: AgentFoldersIn):
    cleaned = []
    for f in body.folders:
        f = f.strip().rstrip("\\/")
        if f and f not in cleaned:
            cleaned.append(f)
    await db.set_setting(AGENT_FOLDERS_KEY, json.dumps(cleaned))
    return await read_agent_folders()


PROMPT_PRESETS_KEY = "prompt_presets"


class PromptPreset(BaseModel):
    name: str
    prompt: str


class PromptPresetsIn(BaseModel):
    presets: list[PromptPreset] = Field(default_factory=list)


@router.get("/settings/prompt-presets")
async def read_prompt_presets():
    try:
        raw = json.loads(await db.get_setting(PROMPT_PRESETS_KEY, "[]"))
    except json.JSONDecodeError:
        raw = []
    presets = [p for p in raw if isinstance(p, dict) and p.get("name")]
    return {"presets": presets}


@router.put("/settings/prompt-presets")
async def write_prompt_presets(body: PromptPresetsIn):
    seen: dict[str, str] = {}
    for p in body.presets:
        name = p.name.strip()[:60]
        if name:
            seen[name] = p.prompt
    presets = [{"name": n, "prompt": pr} for n, pr in seen.items()]
    await db.set_setting(PROMPT_PRESETS_KEY, json.dumps(presets))
    return {"presets": presets}
