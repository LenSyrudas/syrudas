from fastapi import APIRouter

from . import approvals, chat, conversations, mcp, providers

api_router = APIRouter(prefix="/api")
api_router.include_router(conversations.router)
api_router.include_router(providers.router)
api_router.include_router(mcp.router)
api_router.include_router(approvals.router)
api_router.include_router(chat.router)
