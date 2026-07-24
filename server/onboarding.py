"""First-run convenience: auto-configure local model backends.

Probes well-known local OpenAI-compatible servers and creates an instance for
each one that responds, so a fresh install starts with a working model picker.

The "already did this" flag is only set once the user actually has a provider.
Setting it before probing meant a first launch with no backend running burned
the flag permanently: the user would install Ollama, relaunch, and still find an
empty model picker with no way back except adding a provider by hand.
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


async def _probe(name: str, base_url: str) -> tuple[str, str] | None:
    """Return (name, base_url) if a backend answers there with models."""
    try:
        provider = create_provider("openai_compat", {"base_url": base_url})
        models = await asyncio.wait_for(provider.list_models(), timeout=4)
    except Exception:
        return None
    return (name, base_url) if models else None


async def detect_local_providers() -> list[dict]:
    """Probe every known backend and add the ones that answer.

    Ignores the flag - this is the explicit "look again" path used by the UI
    after the user installs a backend. Backends already configured under the
    same base URL are skipped rather than duplicated.
    """
    existing = {
        (inst.get("config") or {}).get("base_url", "").rstrip("/")
        for inst in await db.list_provider_instances()
    }
    # probe concurrently: a firewalled port can sit at the timeout, and two of
    # those in series would stall startup for twice as long
    results = await asyncio.gather(*(_probe(n, u) for n, u in LOCAL_BACKENDS))

    added: list[dict] = []
    for found in results:
        if not found:
            continue
        name, base_url = found
        if base_url.rstrip("/") in existing:
            continue
        inst = await db.create_provider_instance(
            "openai_compat", name, {"base_url": base_url})
        log.info("Auto-configured provider %r at %s", name, base_url)
        added.append(inst)
    return added


async def auto_detect_providers() -> None:
    """Startup hook: detect backends until the user actually has one."""
    if await db.get_setting(FLAG_KEY):
        return
    if await db.list_provider_instances():
        # already set up (manually or by an earlier run) - stop probing, and
        # don't resurrect providers the user has deliberately deleted since
        await db.set_setting(FLAG_KEY, "1")
        return
    if await detect_local_providers():
        await db.set_setting(FLAG_KEY, "1")
