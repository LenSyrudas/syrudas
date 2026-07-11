"""Anthropic (Claude) provider plugin.

Talks to the Anthropic Messages API directly over httpx rather than the
`anthropic` SDK: plugins are loaded dynamically at runtime, so the packaged
exe only guarantees dependencies the core app already bundles (httpx is one).

Configure in Settings with an Anthropic API key. Models are discovered live
from GET /v1/models. Tool calling and streaming are fully supported.

Notes on API mapping (Messages API, anthropic-version 2023-06-01):
- The system prompt is a top-level `system` param, not a message.
- Assistant tool calls are `tool_use` content blocks; tool results are
  `tool_result` blocks inside a user message.
- `max_tokens` is required; defaults to 8192 here (safe on every model) and
  the UI's max-tokens control overrides it.
- When tools are in play, thinking is disabled on models that accept it:
  this app's history does not preserve thinking blocks, and replaying a
  tool-use turn without its thinking block is rejected by thinking-enabled
  models. (Fable/Mythos models reject the disable flag, so they keep
  thinking on; plain chat works, agent mode may not replay cleanly there.)
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Optional

import httpx

from server.providers.base import ConfigField, ModelProvider
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec

API_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MAX_TOKENS = 8192
TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)


def _wire_messages(messages: list[Message]) -> tuple[Optional[str], list[dict]]:
    """Normalized history -> (system, anthropic messages).

    Consecutive tool results merge into a SINGLE user message: the API
    tolerates split messages, but returning parallel tool results separately
    quietly trains the model to stop making parallel calls.
    """
    system: Optional[str] = None
    out: list[dict] = []
    for m in messages:
        if m.role == "system":
            system = m.content if system is None else f"{system}\n\n{m.content}"
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append({
                    "type": "tool_use", "id": tc.id, "name": tc.name,
                    "input": tc.arguments,
                })
            out.append({"role": "assistant", "content": blocks})
        elif m.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m.tool_call_id or "",
                "content": m.content,
            }
            last = out[-1] if out else None
            if (
                last is not None and last["role"] == "user"
                and isinstance(last["content"], list)
                and all(b.get("type") == "tool_result" for b in last["content"])
            ):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m.content:
            out.append({"role": m.role, "content": m.content})
    return system, out


def _wire_tools(tools: list[ToolSpec]) -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in tools
    ]


def _supports_thinking_disable(model: str) -> bool:
    # Fable/Mythos models reject an explicit thinking disable
    lowered = model.lower()
    return "fable" not in lowered and "mythos" not in lowered


# These models reject temperature/top_p with a 400: never forward sampling
# params to them, even if the UI's tuning slider has a persisted value.
_NO_SAMPLING_MARKERS = ("fable", "mythos", "opus-4-7", "opus-4-8", "sonnet-5")


def _supports_sampling(model: str) -> bool:
    lowered = model.lower()
    return not any(marker in lowered for marker in _NO_SAMPLING_MARKERS)


class AnthropicProvider(ModelProvider):
    type_id = "anthropic"
    display_name = "Anthropic (Claude)"
    config_fields = [
        ConfigField(key="api_key", label="API key", type="password", required=True,
                    placeholder="sk-ant-..."),
        ConfigField(key="base_url", label="Base URL (advanced)", type="url",
                    default=DEFAULT_BASE_URL, placeholder=DEFAULT_BASE_URL),
    ]

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    def _headers(self) -> dict:
        return {
            "x-api-key": str(self.config.get("api_key", "")),
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

    def _client_kwargs(self) -> dict:
        kwargs: dict = {"timeout": TIMEOUT}
        if self.config.get("_transport") is not None:  # test hook
            kwargs["transport"] = self.config["_transport"]
        return kwargs

    async def list_models(self) -> list[ModelInfo]:
        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            resp = await client.get(
                f"{self.base_url}/v1/models",
                headers=self._headers(), params={"limit": 100},
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            ModelInfo(id=m["id"], name=m.get("display_name"))
            for m in data.get("data", []) if "id" in m
        ]

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        params: Optional[GenParams] = None,
    ) -> AsyncIterator[StreamEvent]:
        system, wire = _wire_messages(messages)
        body: dict = {
            "model": model,
            "messages": wire,
            "max_tokens": (params.max_tokens if params and params.max_tokens
                           else DEFAULT_MAX_TOKENS),
            "stream": True,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = _wire_tools(tools)
            # this app's history drops thinking blocks; replaying tool-use
            # turns without them breaks thinking-enabled models
            if _supports_thinking_disable(model):
                body["thinking"] = {"type": "disabled"}
        # sampling params 400 on Fable/Mythos/Opus 4.7+/Sonnet 5 - only
        # forward them to models that still accept them
        if params and params.temperature is not None and _supports_sampling(model):
            body["temperature"] = params.temperature

        async for ev in self._stream_once(body, allow_sampling_retry=True):
            yield ev

    async def _stream_once(
        self, body: dict, allow_sampling_retry: bool,
    ) -> AsyncIterator[StreamEvent]:
        input_tokens = 0
        output_tokens = 0
        got_usage = False
        stop_reason: Optional[str] = None
        # tool_use blocks accumulate partial JSON per stream index
        pending: dict[int, dict] = {}

        try:
            async with httpx.AsyncClient(**self._client_kwargs()) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/v1/messages",
                    headers=self._headers(), json=body,
                ) as resp:
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:2000]
                        # safety net for models newer than the marker list:
                        # if a sampling param is rejected, drop it and retry once
                        if (allow_sampling_retry and resp.status_code == 400
                                and ("temperature" in body or "top_p" in body)
                                and ("temperature" in detail or "top_p" in detail)):
                            retry = {k: v for k, v in body.items()
                                     if k not in ("temperature", "top_p")}
                            async for ev in self._stream_once(
                                    retry, allow_sampling_retry=False):
                                yield ev
                            return
                        yield StreamEvent(type="error",
                                          message=f"Anthropic HTTP {resp.status_code}: {detail}")
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            ev = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        etype = ev.get("type")
                        if etype == "message_start":
                            usage = (ev.get("message") or {}).get("usage") or {}
                            input_tokens = usage.get("input_tokens", 0)
                        elif etype == "content_block_start":
                            block = ev.get("content_block") or {}
                            if block.get("type") == "tool_use":
                                pending[ev.get("index", 0)] = {
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "json": "",
                                }
                        elif etype == "content_block_delta":
                            delta = ev.get("delta") or {}
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield StreamEvent(type="text_delta", text=delta["text"])
                            elif delta.get("type") == "input_json_delta":
                                idx = ev.get("index", 0)
                                if idx in pending:
                                    pending[idx]["json"] += delta.get("partial_json", "")
                        elif etype == "content_block_stop":
                            idx = ev.get("index", 0)
                            acc = pending.pop(idx, None)
                            if acc and acc["name"]:
                                try:
                                    args = json.loads(acc["json"]) if acc["json"] else {}
                                except json.JSONDecodeError:
                                    args = {"_raw": acc["json"]}
                                yield StreamEvent(type="tool_call", tool_call=ToolCall(
                                    id=acc["id"] or f"toolu_{idx}",
                                    name=acc["name"], arguments=args))
                        elif etype == "message_delta":
                            delta = ev.get("delta") or {}
                            stop_reason = delta.get("stop_reason") or stop_reason
                            usage = ev.get("usage") or {}
                            if usage.get("output_tokens") is not None:
                                output_tokens = usage["output_tokens"]
                                got_usage = True
                        elif etype == "error":
                            err = ev.get("error") or {}
                            yield StreamEvent(
                                type="error",
                                message=f"Anthropic: {err.get('message', 'unknown error')}")
        except httpx.HTTPError as exc:
            yield StreamEvent(type="error", message=f"Connection error: {exc}")
            return

        if stop_reason == "refusal":
            # HTTP 200 with empty/partial content: without this the user
            # would just see a silent empty reply
            yield StreamEvent(
                type="error",
                message="Claude declined this request for safety reasons.")
        if got_usage or input_tokens:
            yield StreamEvent(type="usage", input_tokens=input_tokens,
                              output_tokens=output_tokens)
        yield StreamEvent(type="done")
