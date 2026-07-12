from fastapi import APIRouter

from . import approvals, attachments, chat, conversations, mcp, memories, providers, settings

api_router = APIRouter(prefix="/api")
api_router.include_router(conversations.router)
api_router.include_router(providers.router)
api_router.include_router(mcp.router)
api_router.include_router(approvals.router)
api_router.include_router(attachments.router)
api_router.include_router(settings.router)
api_router.include_router(memories.router)
api_router.include_router(chat.router)
