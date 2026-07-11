"""OpenAI-compatible API: makes Syrudas a model hub for external tools.

Any OpenAI-compatible client (Continue in VS Code, scripts, other apps) can
point at http://127.0.0.1:8040/v1 and use every model from every configured
provider instance. Model ids are namespaced as "<instance-slug>/<model-id>".
These requests are stateless passthroughs - they don't touch conversations.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import db
from ..providers.registry import create_provider
from ..schemas import GenParams, Message, ToolCall, ToolSpec

router = APIRouter(prefix="/v1", tags=["openai-compat-api"])


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-") or "provider"


async def _instances_by_slug() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for inst in await db.list_provider_instances():
        slug = _slug(inst["name"])
        if slug in out:  # collision: disambiguate with the id prefix
            slug = f"{slug}-{inst['id'][:6]}"
        out[slug] = inst
    return out


def _split_model(model: str, instances: dict[str, dict]) -> tuple[dict, str]:
    if "/" in model:
        slug, _, model_id = model.partition("/")
        if slug in instances and model_id:
            return instances[slug], model_id
    # bare model id: fall back to the first instance (single-backend setups)
    if instances and model:
        return next(iter(instances.values())), model
    raise HTTPException(404, f"Unknown model: {model!r}. GET /v1/models lists valid ids.")


def _normalize_content(content: Any) -> str:
    """OpenAI message content may be a string or a list of typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return "" if content is None else str(content)


def _normalize_messages(raw: list[dict]) -> list[Message]:
    messages = []
    for m in raw:
        role = m.get("role", "user")
        if role not in ("system", "user", "assistant", "tool"):
            role = "user"
        tool_calls = None
        if m.get("tool_calls"):
            tool_calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.get("id", uuid.uuid4().hex), name=fn.get("name", ""), arguments=args))
        messages.append(Message(
            role=role,
            content=_normalize_content(m.get("content")),
            tool_calls=tool_calls,
            tool_call_id=m.get("tool_call_id"),
        ))
    return messages


def _normalize_tools(raw: Optional[list[dict]]) -> Optional[list[ToolSpec]]:
    if not raw:
        return None
    tools = []
    for t in raw:
        fn = t.get("function") or {}
        if fn.get("name"):
            tools.append(ToolSpec(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=fn.get("parameters") or {"type": "object", "properties": {}},
            ))
    return tools or None


@router.get("/models")
async def list_models():
    data = []
    for slug, inst in (await _instances_by_slug()).items():
        try:
            provider = create_provider(inst["type_id"], inst["config"])
            models = await provider.list_models()
        except Exception:
            continue  # unreachable backend: skip its models
        for m in models:
            data.append({
                "id": f"{slug}/{m.id}",
                "object": "model",
                "created": 0,
                "owned_by": inst["name"],
            })
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(body: dict):
    instances = await _instances_by_slug()
    inst, model_id = _split_model(str(body.get("model", "")), instances)
    provider = create_provider(inst["type_id"], inst["config"])

    messages = _normalize_messages(body.get("messages") or [])
    tools = _normalize_tools(body.get("tools"))
    params = GenParams(
        temperature=body.get("temperature"),
        max_tokens=body.get("max_tokens"),
        top_p=body.get("top_p"),
    )
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    wire_model = body.get("model", model_id)

    def _chunk(delta: dict, finish: Optional[str] = None, usage: Optional[dict] = None) -> str:
        payload: dict = {
            "id": completion_id, "object": "chat.completion.chunk",
            "created": created, "model": wire_model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage:
            payload["usage"] = usage
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    if body.get("stream"):
        async def sse():
            tool_index = 0
            finish = "stop"
            usage = None
            async for ev in provider.chat(model_id, messages, tools=tools, params=params):
                if ev.type == "text_delta" and ev.text:
                    yield _chunk({"content": ev.text})
                elif ev.type == "tool_call" and ev.tool_call:
                    finish = "tool_calls"
                    yield _chunk({"tool_calls": [{
                        "index": tool_index,
                        "id": ev.tool_call.id,
                        "type": "function",
                        "function": {
                            "name": ev.tool_call.name,
                            "arguments": json.dumps(ev.tool_call.arguments),
                        },
                    }]})
                    tool_index += 1
                elif ev.type == "usage":
                    usage = {
                        "prompt_tokens": ev.input_tokens or 0,
                        "completion_tokens": ev.output_tokens or 0,
                        "total_tokens": (ev.input_tokens or 0) + (ev.output_tokens or 0),
                    }
                elif ev.type == "error":
                    yield _chunk({"content": f"\n[Syrudas error: {ev.message}]"})
            yield _chunk({}, finish=finish, usage=usage)
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    # non-streaming
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    usage = None
    error = None
    async for ev in provider.chat(model_id, messages, tools=tools, params=params):
        if ev.type == "text_delta" and ev.text:
            text_parts.append(ev.text)
        elif ev.type == "tool_call" and ev.tool_call:
            tool_calls.append(ev.tool_call)
        elif ev.type == "usage":
            usage = {
                "prompt_tokens": ev.input_tokens or 0,
                "completion_tokens": ev.output_tokens or 0,
                "total_tokens": (ev.input_tokens or 0) + (ev.output_tokens or 0),
            }
        elif ev.type == "error":
            error = ev.message
    if error and not text_parts and not tool_calls:
        raise HTTPException(502, f"Provider error: {error}")

    message: dict = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        message["tool_calls"] = [{
            "id": tc.id, "type": "function",
            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
        } for tc in tool_calls]
    return {
        "id": completion_id, "object": "chat.completion",
        "created": created, "model": wire_model,
        "choices": [{
            "index": 0, "message": message,
            "finish_reason": "tool_calls" if tool_calls else "stop",
        }],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
