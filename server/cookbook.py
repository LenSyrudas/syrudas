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

# Curated Ollama models. Footprints are rough Q4 estimates in GB.
CATALOG: list[dict] = [
    {"name": "llama3.2:1b", "params": "1B", "size_gb": 1.3, "min_vram_gb": 2, "min_ram_gb": 4,
     "tags": ["chat", "tools"], "blurb": "Tiny and fast - runs on almost anything."},
    {"name": "llama3.2:3b", "params": "3B", "size_gb": 2.0, "min_vram_gb": 4, "min_ram_gb": 6,
     "tags": ["chat", "tools"], "blurb": "Small all-rounder with tool use."},
    {"name": "qwen2.5:3b", "params": "3B", "size_gb": 1.9, "min_vram_gb": 4, "min_ram_gb": 6,
     "tags": ["chat", "tools", "code"], "blurb": "Capable small model, decent at code."},
    {"name": "phi3.5:3.8b", "params": "3.8B", "size_gb": 2.2, "min_vram_gb": 4, "min_ram_gb": 6,
     "tags": ["chat", "reasoning"], "blurb": "Punches above its size on reasoning."},
    {"name": "llama3.1:8b", "params": "8B", "size_gb": 4.7, "min_vram_gb": 6, "min_ram_gb": 10,
     "tags": ["chat", "tools"], "blurb": "Strong general-purpose model with good tool use."},
    {"name": "qwen2.5:7b", "params": "7B", "size_gb": 4.7, "min_vram_gb": 6, "min_ram_gb": 10,
     "tags": ["chat", "tools", "code"], "blurb": "Excellent 7B all-rounder."},
    {"name": "mistral:7b", "params": "7B", "size_gb": 4.1, "min_vram_gb": 6, "min_ram_gb": 10,
     "tags": ["chat", "tools"], "blurb": "Fast, reliable 7B classic."},
    {"name": "qwen2.5-coder:7b", "params": "7B", "size_gb": 4.7, "min_vram_gb": 6, "min_ram_gb": 10,
     "tags": ["code", "tools"], "blurb": "Coding-focused; good for the agent."},
    {"name": "deepseek-r1:8b", "params": "8B", "size_gb": 4.9, "min_vram_gb": 6, "min_ram_gb": 10,
     "tags": ["chat", "reasoning"], "blurb": "Reasoning-tuned distilled model."},
    {"name": "gemma2:9b", "params": "9B", "size_gb": 5.4, "min_vram_gb": 8, "min_ram_gb": 12,
     "tags": ["chat"], "blurb": "Google's strong 9B chat model."},
    {"name": "llava:7b", "params": "7B", "size_gb": 4.7, "min_vram_gb": 6, "min_ram_gb": 10,
     "tags": ["chat", "vision"], "blurb": "Multimodal - can look at images."},
    {"name": "qwen2.5:14b", "params": "14B", "size_gb": 9.0, "min_vram_gb": 12, "min_ram_gb": 18,
     "tags": ["chat", "tools", "code"], "blurb": "Bigger, sharper - needs a real GPU."},
    {"name": "gemma2:27b", "params": "27B", "size_gb": 16.0, "min_vram_gb": 20, "min_ram_gb": 32,
     "tags": ["chat"], "blurb": "High quality; wants 24GB-class VRAM."},
    {"name": "qwen2.5:32b", "params": "32B", "size_gb": 20.0, "min_vram_gb": 24, "min_ram_gb": 40,
     "tags": ["chat", "tools", "code"], "blurb": "Near-frontier local model for big GPUs."},
    {"name": "nomic-embed-text", "params": "0.1B", "size_gb": 0.3, "min_vram_gb": 2, "min_ram_gb": 2,
     "tags": ["embedding"], "blurb": "Embeddings for Knowledge / RAG."},
    {"name": "mxbai-embed-large", "params": "0.3B", "size_gb": 0.7, "min_vram_gb": 2, "min_ram_gb": 4,
     "tags": ["embedding"], "blurb": "Higher-quality embeddings for RAG."},
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
            return ("unknown",
                    "Your GPU's VRAM couldn't be measured exactly - this may or may not fit.")
        if need_vram <= vram * 0.9:
            return ("good", "Fits comfortably on your GPU.")
        if need_vram <= vram:
            return ("tight", "Fits on your GPU with little headroom.")
        # too big for the GPU alone - fall through to CPU/RAM

    if ram is not None:
        if entry["min_ram_gb"] <= ram * 0.7:
            where = "with CPU offload" if vram else "on the CPU"
            return ("cpu", f"Runs {where} - slower than a model that fits your GPU.")
        if entry["min_ram_gb"] <= ram:
            return ("tight", "Will run but may be slow or memory-tight.")
        return ("too_big", "Likely too large for this machine's memory.")

    return ("unknown", "Not enough hardware info to judge fit.")


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
