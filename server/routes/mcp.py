from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db

router = APIRouter(tags=["mcp"])


class McpServerIn(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class McpServerPatch(BaseModel):
    name: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    enabled: bool | None = None


@router.get("/mcp-servers")
async def list_servers():
    return await db.list_mcp_servers()


@router.post("/mcp-servers")
async def create_server(body: McpServerIn):
    return await db.create_mcp_server(body.name, body.command, body.args, body.env)


@router.patch("/mcp-servers/{server_id}")
async def patch_server(server_id: str, patch: McpServerPatch):
    fields = patch.model_dump(exclude_none=True)
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    await db.update_mcp_server(server_id, **fields)
    servers = {s["id"]: s for s in await db.list_mcp_servers()}
    if server_id not in servers:
        raise HTTPException(404, "MCP server not found")
    return servers[server_id]


@router.delete("/mcp-servers/{server_id}")
async def delete_server(server_id: str):
    await db.delete_mcp_server(server_id)
    return {"ok": True}
