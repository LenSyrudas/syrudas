"""First-run convenience: auto-configure local model backends.

Runs once (guarded by a settings flag) when no providers are configured yet.
Probes well-known local OpenAI-compatible servers and creates an instance for
each one that responds, so a fresh install starts with a working model picker.
"""
from __future__ import annotations

import asyncio
import logging

from . import db
from .providers.registry import create_provider

log = logging.getLogger(__name__)

LOCAL_BACKENDS = [
    ("Ollama local", "http://localhost:11434/v1"),
    ("LM Studio local", "http://localhost:1234/v1"),
]

FLAG_KEY = "auto_detect_done"


async def auto_detect_providers() -> None:
    if await db.get_setting(FLAG_KEY):
        return
    await db.set_setting(FLAG_KEY, "1")
    if await db.list_provider_instances():
        return

    for name, base_url in LOCAL_BACKENDS:
        try:
            provider = create_provider("openai_compat", {"base_url": base_url})
            models = await asyncio.wait_for(provider.list_models(), timeout=4)
        except Exception:
            continue
        if models:
            await db.create_provider_instance(
                "openai_compat", name, {"base_url": base_url})
            log.info("Auto-configured provider %r (%d models at %s)",
                     name, len(models), base_url)
