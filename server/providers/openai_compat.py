"""OpenAI-compatible chat-completions adapter.

Covers any backend speaking the OpenAI dialect: Ollama, LM Studio, llama.cpp
server, vLLM, OpenRouter, OpenAI itself. Point base_url at the /v1 root.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

import httpx

from ..schemas import GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec
from .base import ConfigField, ModelProvider

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)
# a context probe must never delay a turn; if the backend is slow, go without
OLLAMA_PROBE_TIMEOUT = httpx.Timeout(5.0)

# (base_url, model) -> tokens, or 0 for "asked and found nothing". A model's
# context does not change under a running server, and this is consulted on
# every turn.
_CONTEXT_CACHE: dict[tuple[str, str], int] = {}


def _context_from_entry(entry: dict) -> Optional[int]:
    """Pull a context length out of a /v1/models entry if the backend gives one.

    OpenRouter reports it, at the top level and again under top_provider;
    stock OpenAI and Ollama do not.
    """
    for value in (entry.get("context_length"),
                  (entry.get("top_provider") or {}).get("context_length")):
        if isinstance(value, int) and value > 0:
            return value
    return None


def _wire_messages(messages: list[Message]) -> list[dict]:
    out = []
    for m in messages:
        d: dict = {"role": m.role, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ]
        if m.role == "tool" and m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        out.append(d)
    return out


def _wire_tools(tools: list[ToolSpec]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


class OpenAICompatProvider(ModelProvider):
    type_id = "openai_compat"
    display_name = "OpenAI-compatible (Ollama, LM Studio, OpenRouter, OpenAI, vLLM...)"
    config_fields = [
        ConfigField(
            key="base_url", label="Base URL", type="url", required=True,
            placeholder="http://localhost:11434/v1",
        ),
        ConfigField(key="api_key", label="API key (if required)", type="password"),
    ]

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url", "")).rstrip("/")

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        api_key = self.config.get("api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    async def list_models(self) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(f"{self.base_url}/models", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        models = data.get("data", data if isinstance(data, list) else [])
        return [
            ModelInfo(id=m["id"], name=m.get("name"), context_tokens=_context_from_entry(m))
            for m in models if "id" in m
        ]

    async def context_tokens(self, model: str) -> Optional[int]:
        """Ask the backend how much context this model actually has.

        Two sources, because no single one covers the backends this adapter
        serves. OpenRouter reports `context_length` in its model listing.
        Ollama's OpenAI-compatible listing does not, but its native /api/show
        does - and Ollama loads models at their full architectural context by
        default, so that number is the window in force. Anything else returns
        None and the caller uses a fixed budget.
        """
        cached = _CONTEXT_CACHE.get((self.base_url, model))
        if cached is not None:
            return cached or None  # 0 is the cached "asked, nothing to find"

        found: Optional[int] = None
        try:
            for info in await self.list_models():
                if info.id == model and info.context_tokens:
                    found = info.context_tokens
                    break
        except Exception:
            log.debug("Model listing failed while resolving context for %s", model)

        if found is None:
            found = await self._ollama_context(model)

        _CONTEXT_CACHE[(self.base_url, model)] = found or 0
        return found

    async def _ollama_context(self, model: str) -> Optional[int]:
        """Ollama's native /api/show, reached by dropping the /v1 suffix."""
        root = self.base_url[: -len("/v1")] if self.base_url.endswith("/v1") else None
        if not root:
            return None
        try:
            async with httpx.AsyncClient(timeout=OLLAMA_PROBE_TIMEOUT) as client:
                resp = await client.post(f"{root}/api/show", json={"model": model})
                if resp.status_code != 200:
                    return None
                info = resp.json().get("model_info") or {}
        except (httpx.HTTPError, ValueError):
            return None
        # the key is architecture-prefixed: llama.context_length, qwen2.context_length
        for key, value in info.items():
            if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                return value
        return None

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """OpenAI-compatible /embeddings (works with Ollama, LM Studio, OpenAI...)."""
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers=self._headers(),
                json={"model": model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        # servers should preserve order, but the spec keys results by index
        rows = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        return [r["embedding"] for r in rows]

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        params: Optional[GenParams] = None,
    ) -> AsyncIterator[StreamEvent]:
        body: dict = {
            "model": model,
            "messages": _wire_messages(messages),
            "stream": True,
            # ask for token counts in the final stream chunk; Ollama/LM Studio
            # send them anyway, OpenAI proper only does with this flag set
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = _wire_tools(tools)
        if params:
            if params.temperature is not None:
                body["temperature"] = params.temperature
            if params.max_tokens is not None:
                body["max_tokens"] = params.max_tokens
            if params.top_p is not None:
                body["top_p"] = params.top_p

        # tool-call fragments accumulate per stream index until the stream ends
        pending_calls: dict[int, dict] = {}

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/chat/completions",
                    headers=self._headers(), json=body,
                ) as resp:
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:2000]
                        yield StreamEvent(type="error", message=f"HTTP {resp.status_code}: {detail}")
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        usage = chunk.get("usage")
                        if usage:
                            yield StreamEvent(
                                type="usage",
                                input_tokens=usage.get("prompt_tokens"),
                                output_tokens=usage.get("completion_tokens"),
                            )
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                yield StreamEvent(type="text_delta", text=delta["content"])
                            for frag in delta.get("tool_calls") or []:
                                idx = frag.get("index", 0)
                                acc = pending_calls.setdefault(
                                    idx, {"id": "", "name": "", "arguments": ""}
                                )
                                if frag.get("id"):
                                    acc["id"] = frag["id"]
                                fn = frag.get("function") or {}
                                if fn.get("name"):
                                    acc["name"] += fn["name"]
                                if fn.get("arguments"):
                                    acc["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            yield StreamEvent(type="error", message=f"Connection error: {exc}")
            return

        for idx in sorted(pending_calls):
            acc = pending_calls[idx]
            if not acc["name"]:
                continue
            try:
                args = json.loads(acc["arguments"]) if acc["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": acc["arguments"]}
            yield StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id=acc["id"] or f"call_{idx}", name=acc["name"], arguments=args),
            )
        yield StreamEvent(type="done")
