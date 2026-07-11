"""Google Gemini provider plugin.

Talks to the Gemini REST API (generativelanguage.googleapis.com, v1beta)
directly over httpx, so it works as a drop-in plugin even inside the packaged
exe. Configure in Settings with an API key from https://aistudio.google.com/.
Models are discovered live and filtered to ones that support generateContent.

Notes on API mapping:
- History maps to `contents` with roles "user"/"model"; the system prompt goes
  in `systemInstruction`.
- Tool calls are `functionCall` parts (no ids on the wire - ids are
  synthesized here and mapped back by call order); tool results are
  `functionResponse` parts in a user turn, which need the function NAME, so a
  call-id -> name map is built while walking the history.
- Function-declaration schemas reject JSON-Schema keys Gemini doesn't know
  (`additionalProperties`, `$schema`), so those are stripped recursively.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Optional

import httpx

from server.providers.base import ConfigField, ModelProvider
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)

_UNSUPPORTED_SCHEMA_KEYS = {"additionalProperties", "$schema", "$defs", "$ref"}


def _clean_schema(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {
            k: _clean_schema(v) for k, v in schema.items()
            if k not in _UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]
    return schema


def _wire_contents(messages: list[Message]) -> tuple[Optional[str], list[dict]]:
    """Normalized history -> (system_instruction, gemini contents)."""
    system: Optional[str] = None
    contents: list[dict] = []
    call_names: dict[str, str] = {}  # tool_call_id -> function name

    def _append(role: str, part: dict) -> None:
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append(part)
        else:
            contents.append({"role": role, "parts": [part]})

    for m in messages:
        if m.role == "system":
            system = m.content if system is None else f"{system}\n\n{m.content}"
        elif m.role == "assistant":
            if m.content:
                _append("model", {"text": m.content})
            for tc in m.tool_calls or []:
                call_names[tc.id] = tc.name
                _append("model", {"functionCall": {"name": tc.name, "args": tc.arguments}})
        elif m.role == "tool":
            name = call_names.get(m.tool_call_id or "", "tool")
            _append("user", {"functionResponse": {
                "name": name, "response": {"output": m.content or ""},
            }})
        elif m.content:
            _append("user", {"text": m.content})
    return system, contents


def _wire_tools(tools: list[ToolSpec]) -> list[dict]:
    return [{
        "functionDeclarations": [
            {
                "name": t.name,
                "description": t.description,
                "parameters": _clean_schema(t.parameters),
            }
            for t in tools
        ],
    }]


class GeminiProvider(ModelProvider):
    type_id = "gemini"
    display_name = "Google (Gemini)"
    config_fields = [
        ConfigField(key="api_key", label="API key", type="password", required=True,
                    placeholder="AIza..."),
        ConfigField(key="base_url", label="Base URL (advanced)", type="url",
                    default=DEFAULT_BASE_URL, placeholder=DEFAULT_BASE_URL),
    ]

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    def _headers(self) -> dict:
        return {
            "x-goog-api-key": str(self.config.get("api_key", "")),
            "Content-Type": "application/json",
        }

    def _client_kwargs(self) -> dict:
        kwargs: dict = {"timeout": TIMEOUT}
        if self.config.get("_transport") is not None:  # test hook
            kwargs["transport"] = self.config["_transport"]
        return kwargs

    async def list_models(self) -> list[ModelInfo]:
        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            resp = await client.get(f"{self.base_url}/models",
                                    headers=self._headers(),
                                    params={"pageSize": 1000})
            resp.raise_for_status()
            data = resp.json()
        models = []
        for m in data.get("models", []):
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            model_id = str(m.get("name", "")).removeprefix("models/")
            if model_id:
                models.append(ModelInfo(id=model_id, name=m.get("displayName")))
        return models

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        params: Optional[GenParams] = None,
    ) -> AsyncIterator[StreamEvent]:
        system, contents = _wire_contents(messages)
        body: dict = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = _wire_tools(tools)
        gen_config: dict = {}
        if params:
            if params.temperature is not None:
                gen_config["temperature"] = params.temperature
            if params.max_tokens is not None:
                gen_config["maxOutputTokens"] = params.max_tokens
            if params.top_p is not None:
                gen_config["topP"] = params.top_p
        if gen_config:
            body["generationConfig"] = gen_config

        url = f"{self.base_url}/models/{model}:streamGenerateContent"
        prompt_tokens: Optional[int] = None
        output_tokens: Optional[int] = None
        finish_reason: Optional[str] = None
        got_content = False

        try:
            async with httpx.AsyncClient(**self._client_kwargs()) as client:
                async with client.stream(
                    "POST", url, headers=self._headers(),
                    params={"alt": "sse"}, json=body,
                ) as resp:
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:2000]
                        yield StreamEvent(type="error",
                                          message=f"Gemini HTTP {resp.status_code}: {detail}")
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            chunk = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        usage = chunk.get("usageMetadata") or {}
                        if usage.get("promptTokenCount") is not None:
                            prompt_tokens = usage["promptTokenCount"]
                        if usage.get("candidatesTokenCount") is not None:
                            output_tokens = usage["candidatesTokenCount"]
                        for cand in chunk.get("candidates") or []:
                            finish_reason = cand.get("finishReason") or finish_reason
                            for part in (cand.get("content") or {}).get("parts") or []:
                                if part.get("text"):
                                    got_content = True
                                    yield StreamEvent(type="text_delta", text=part["text"])
                                elif part.get("functionCall"):
                                    got_content = True
                                    fc = part["functionCall"]
                                    yield StreamEvent(type="tool_call", tool_call=ToolCall(
                                        id=f"gem_{uuid.uuid4().hex[:12]}",
                                        name=fc.get("name", ""),
                                        arguments=fc.get("args") or {},
                                    ))
        except httpx.HTTPError as exc:
            yield StreamEvent(type="error", message=f"Connection error: {exc}")
            return

        if not got_content and finish_reason in ("SAFETY", "PROHIBITED_CONTENT", "RECITATION"):
            yield StreamEvent(
                type="error",
                message=f"Gemini declined this request ({finish_reason.lower()}).")
        if prompt_tokens is not None or output_tokens is not None:
            yield StreamEvent(type="usage", input_tokens=prompt_tokens,
                              output_tokens=output_tokens)
        yield StreamEvent(type="done")
