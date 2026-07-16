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
        return [ModelInfo(id=m["id"], name=m.get("name")) for m in models if "id" in m]

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
