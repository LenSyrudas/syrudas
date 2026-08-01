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


# A probe has three outcomes, not two. Collapsing "answered but has no models"
# into "nothing there" is why the commonest first run - Ollama installed,
# nothing pulled yet - told the user to start the backend that was already
# running, which is unactionable advice for the exact state most people are in.
NO_BACKEND = "no_backend"
NO_MODELS = "no_models"
READY = "ready"


async def _probe(name: str, base_url: str) -> tuple[str, str, str]:
    """Return (outcome, name, base_url) for one well-known backend."""
    try:
        provider = create_provider("openai_compat", {"base_url": base_url})
        models = await asyncio.wait_for(provider.list_models(), timeout=4)
    except Exception:
        return (NO_BACKEND, name, base_url)
    return (READY if models else NO_MODELS, name, base_url)


async def probe_backends() -> dict:
    """What the well-known local backends look like right now.

    Used by the UI to say something true and actionable on an empty first run.
    """
    results = await asyncio.gather(*(_probe(n, u) for n, u in LOCAL_BACKENDS))
    running = [(n, u) for outcome, n, u in results if outcome != NO_BACKEND]
    ready = [(n, u) for outcome, n, u in results if outcome == READY]
    if ready:
        state = READY
    elif running:
        state = NO_MODELS
    else:
        state = NO_BACKEND
    return {
        "state": state,
        "running": [{"name": n, "base_url": u} for n, u in running],
        "hint": {
            READY: "A local model backend is running and has models.",
            NO_MODELS: (
                f"{running[0][0].split()[0] if running else 'A backend'} is running but has "
                "no models yet. Pull one first, for example: ollama pull llama3.1:8b"),
            NO_BACKEND: (
                "No local model backend answered. Install and start Ollama "
                "(https://ollama.com) or LM Studio, then look again."),
        }[state],
    }


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
    for outcome, name, base_url in results:
        # only a backend that actually serves models is worth configuring: an
        # instance pointing at an empty one puts a broken entry in the picker
        if outcome != READY:
            continue
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
