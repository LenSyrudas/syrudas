"""Model cookbook (Ollama-first): a curated catalog, hardware-aware fit
ratings, and pull/list/delete against a local Ollama.

The cookbook is additive - it never replaces the provider-plugin system. It
helps you get models into Ollama; the models you pull then appear through the
normal OpenAI-compatible provider and the usual model picker. The Ollama base
URL is discovered from your configured providers (or the localhost default),
so nothing here hardcodes Ollama as *the* backend.

Fit ratings are estimates: real VRAM/RAM use depends on quantization and
context length, so the UI presents them as guidance, not guarantees.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncIterator, Optional

import httpx

from . import db
from .hardware import detect_hardware

log = logging.getLogger(__name__)

# Ollama model names: "llama3.1:8b", "library/qwen2.5", "nomic-embed-text"
MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,120}(:[a-zA-Z0-9._-]{1,64})?$")
_VERSION_TIMEOUT = httpx.Timeout(4.0)
_TAGS_TIMEOUT = httpx.Timeout(8.0)
# a pull can take many minutes; no read timeout, just a connect budget
_PULL_TIMEOUT = httpx.Timeout(10.0, read=None)

# A few suggested starting points, one per size band, NOT a model directory.
#
# This list can only be changed by editing source, rebuilding the exe and
# cutting a release, so anything specific dates badly - a long catalog is a
# promise to keep current that the delivery mechanism cannot honour. The pull
# endpoint accepts any valid name, including hf.co/... references, so the
# free-text box is the way to get anything not listed here.
#
# Footprints are rough Q4 estimates in GB.
CATALOG: list[dict] = [
    {"name": "llama3.2:3b", "params": "3B", "size_gb": 2.0, "min_vram_gb": 4, "min_ram_gb": 6,
     "tags": ["chat", "tools"], "blurb": "Small all-rounder with tool use."},
    {"name": "llama3.1:8b", "params": "8B", "size_gb": 4.7, "min_vram_gb": 6, "min_ram_gb": 10,
     "tags": ["chat", "tools"], "blurb": "Strong general-purpose model with good tool use."},
    {"name": "qwen2.5-coder:7b", "params": "7B", "size_gb": 4.7, "min_vram_gb": 6, "min_ram_gb": 10,
     "tags": ["code", "tools"], "blurb": "Coding-focused; good for the agent."},
    # No vision models here on purpose: message content is text-only end to end
    # (see the whitepaper's limitations), and the attachment endpoint rejects
    # images outright. Recommending a model for a capability the app cannot
    # accept input for is worse than not listing it.
    {"name": "qwen2.5:14b", "params": "14B", "size_gb": 9.0, "min_vram_gb": 12, "min_ram_gb": 18,
     "tags": ["chat", "tools", "code"], "blurb": "Bigger, sharper - needs a real GPU."},
    {"name": "nomic-embed-text", "params": "0.1B", "size_gb": 0.3, "min_vram_gb": 2, "min_ram_gb": 2,
     "tags": ["embedding"], "blurb": "Embeddings for Knowledge / RAG."},
]


# --- hardware-aware fit ---

def _best_vram_gb(hw: dict) -> tuple[Optional[float], bool]:
    """Largest GPU VRAM in GB and whether that figure is a rough estimate
    (WMI-derived, possibly capped at ~4 GB)."""
    best_mb = None
    estimated = False
    for g in hw.get("gpus", []) or []:
        mb = g.get("vram_total_mb")
        if mb and (best_mb is None or mb > best_mb):
            best_mb = mb
            estimated = bool(g.get("vram_estimated")) or bool(g.get("vram_capped"))
    return (round(best_mb / 1024, 1) if best_mb else None, estimated)


def _ram_gb(hw: dict) -> Optional[float]:
    mb = (hw.get("ram") or {}).get("total_mb")
    return round(mb / 1024, 1) if mb else None


def rate_fit(hw: dict, entry: dict) -> tuple[str, str]:
    """(fit, reason). fit in {good, tight, cpu, too_big, unknown}."""
    vram, vram_est = _best_vram_gb(hw)
    ram = _ram_gb(hw)
    need_vram = entry["min_vram_gb"]

    if vram is not None:
        if vram_est and need_vram > vram:
            # An estimated VRAM figure that looks too small used to end here,
            # returning "unknown" for every entry - so an integrated GPU with
            # 32 GB of system RAM got a page of shrugs, strictly worse than a
            # machine reporting no GPU at all, which at least got RAM answers.
            # Fall through to the RAM branch and carry the caveat instead.
            fit, reason = _rate_on_ram(ram, entry, vram=vram)
            return (fit, f"{reason} (Your GPU's VRAM could not be measured exactly.)")
        if need_vram <= vram * 0.9:
            return ("good", "Fits comfortably on your GPU.")
        if need_vram <= vram:
            return ("tight", "Fits on your GPU with little headroom.")
        # too big for the GPU alone - fall through to CPU/RAM

    return _rate_on_ram(ram, entry, vram=vram)


def _rate_on_ram(ram: float | None, entry: dict, vram: float | None) -> tuple[str, str]:
    """Judge on system memory - the answer whenever the GPU cannot decide it."""
    if ram is None:
        return ("unknown", "Not enough hardware info to judge fit.")
    if entry["min_ram_gb"] <= ram * 0.7:
        where = "with CPU offload" if vram else "on the CPU"
        return ("cpu", f"Runs {where} - slower than a model that fits your GPU.")
    if entry["min_ram_gb"] <= ram:
        return ("tight", "Will run but may be slow or memory-tight.")
    return ("too_big", "Likely too large for this machine's memory.")


def _installed_match(entry_name: str, installed: list[str]) -> bool:
    """Ollama tags are like 'llama3.1:8b' or 'nomic-embed-text:latest'."""
    for name in installed:
        if name == entry_name:
            return True
        # a tag-less catalog entry is what `ollama pull` stores as ':latest';
        # match only that, NOT some other tag like ':v1.5' (Remove would 404)
        if ":" not in entry_name and name == f"{entry_name}:latest":
            return True
    return False


# --- Ollama native API (separate from the OpenAI-compatible chat adapter) ---

def _ollama_root(base_url: str) -> str:
    """Turn a provider base_url (…:11434/v1) into the Ollama API root (…:11434)."""
    root = base_url.strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root.rstrip("/")


async def resolve_ollama_base() -> Optional[str]:
    """Find a reachable Ollama by probing configured providers' hosts, then the
    localhost default. Returns the API root, or None if none responds."""
    # localhost first: it's the common case and refuses instantly when absent,
    # so an offline configured remote host can't stall the probe for seconds
    candidates: list[str] = ["http://localhost:11434"]
    for inst in await db.list_provider_instances():
        base = (inst.get("config") or {}).get("base_url")
        if base:
            candidates.append(_ollama_root(base))

    seen: set[str] = set()
    for root in candidates:
        if not root or root in seen:
            continue
        seen.add(root)
        try:
            async with httpx.AsyncClient(timeout=_VERSION_TIMEOUT) as client:
                resp = await client.get(f"{root}/api/version")
            if resp.status_code == 200 and "version" in resp.json():
                return root
        except Exception:
            continue
    return None


async def ollama_installed(base: str) -> list[str]:
    async with httpx.AsyncClient(timeout=_TAGS_TIMEOUT) as client:
        resp = await client.get(f"{base}/api/tags")
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", []) if "name" in m]


async def ollama_pull(base: str, name: str) -> AsyncIterator[dict]:
    """Stream raw Ollama pull-progress JSON objects."""
    async with httpx.AsyncClient(timeout=_PULL_TIMEOUT) as client:
        async with client.stream(
            "POST", f"{base}/api/pull", json={"name": name, "stream": True}
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.strip():
                    yield json.loads(line)


async def ollama_delete(base: str, name: str) -> None:
    async with httpx.AsyncClient(timeout=_TAGS_TIMEOUT) as client:
        resp = await client.request("DELETE", f"{base}/api/delete", json={"name": name})
        resp.raise_for_status()


# --- assembly ---

async def build_cookbook() -> dict:
    # detection shells out (nvidia-smi, PowerShell) - keep it off the event loop
    hw = await asyncio.to_thread(detect_hardware)
    base = await resolve_ollama_base()
    installed: list[str] = []
    if base:
        try:
            installed = await ollama_installed(base)
        except Exception:
            log.debug("listing installed Ollama models failed", exc_info=True)

    catalog = []
    for entry in CATALOG:
        fit, reason = rate_fit(hw, entry)
        catalog.append({
            **entry,
            "fit": fit,
            "fit_reason": reason,
            "installed": _installed_match(entry["name"], installed),
        })

    return {
        "hardware": hw,
        "ollama": {"configured": bool(base), "base_url": base},
        "installed": installed,
        "catalog": catalog,
    }
