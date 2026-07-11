"""Offline tests for the Anthropic provider plugin.

Uses httpx.MockTransport via the plugin's `_transport` config hook - no
network, no API key. Verifies wire-format translation both directions:
request bodies match the Messages API shape, and documented SSE streams
parse into the correct normalized events.
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("anthropic_plugin", ROOT / "plugins" / "anthropic.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

from server.schemas import GenParams, Message, ToolCall, ToolSpec  # noqa: E402


def sse(events: list[dict]) -> bytes:
    return b"".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events
    )


CHAT_SSE = sse([
    {"type": "message_start", "message": {"usage": {"input_tokens": 42}}},
    # thinking block: must be ignored entirely
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "SECRET REASONING"}},
    {"type": "content_block_stop", "index": 0},
    # text block
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello "}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "world"}},
    {"type": "content_block_stop", "index": 1},
    # tool_use block with split JSON
    {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_abc", "name": "get_weather"}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"city": "Pa'}},
    {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": 'ris"}'}},
    {"type": "content_block_stop", "index": 2},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 17}},
    {"type": "message_stop"},
])

REFUSAL_SSE = sse([
    {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
    {"type": "message_delta", "delta": {"stop_reason": "refusal"}, "usage": {"output_tokens": 0}},
    {"type": "message_stop"},
])

captured: dict = {}


def handler(request: httpx.Request) -> httpx.Response:
    captured["headers"] = dict(request.headers)
    if request.url.path == "/v1/models":
        return httpx.Response(200, json={"data": [
            {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"},
            {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5"},
        ]})
    captured["body"] = json.loads(request.content)
    if captured.get("respond_refusal"):
        return httpx.Response(200, content=REFUSAL_SSE)
    return httpx.Response(200, content=CHAT_SSE)


def make_provider() -> "plugin.AnthropicProvider":
    return plugin.AnthropicProvider({
        "api_key": "sk-ant-test",
        "base_url": "https://mock.local",
        "_transport": httpx.MockTransport(handler),
    })


HISTORY = [
    Message(role="system", content="You are terse."),
    Message(role="user", content="What's the weather in Paris and London?"),
    Message(role="assistant", content="Checking both.", tool_calls=[
        ToolCall(id="toolu_1", name="get_weather", arguments={"city": "Paris"}),
        ToolCall(id="toolu_2", name="get_weather", arguments={"city": "London"}),
    ]),
    Message(role="tool", content="18C sunny", tool_call_id="toolu_1"),
    Message(role="tool", content="14C rain", tool_call_id="toolu_2"),
    Message(role="user", content="Thanks, now just Paris again"),
]

TOOLS = [ToolSpec(name="get_weather", description="Get weather",
                  parameters={"type": "object", "properties": {"city": {"type": "string"}},
                              "required": ["city"]})]


async def main() -> int:
    provider = make_provider()

    # --- list_models ---
    models = await provider.list_models()
    assert [m.id for m in models] == ["claude-opus-4-8", "claude-haiku-4-5"], models
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    print("list_models: OK")

    # --- request wire format ---
    events = []
    async for ev in provider.chat("claude-opus-4-8", HISTORY, tools=TOOLS,
                                  params=GenParams(temperature=0.3, max_tokens=2000)):
        events.append(ev)
    body = captured["body"]
    assert body["system"] == "You are terse.", body.get("system")
    assert body["max_tokens"] == 2000
    assert "temperature" not in body, "temperature must be stripped for opus-4-8"
    assert body["thinking"] == {"type": "disabled"}, "thinking should be disabled with tools"
    assert body["tools"][0]["input_schema"]["required"] == ["city"]
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user", "user"], roles
    # parallel tool results merged into ONE user message
    tool_result_msg = body["messages"][2]
    kinds = [b["type"] for b in tool_result_msg["content"]]
    assert kinds == ["tool_result", "tool_result"], kinds
    assert tool_result_msg["content"][0]["tool_use_id"] == "toolu_1"
    # assistant tool_use blocks present
    asst = body["messages"][1]["content"]
    assert [b["type"] for b in asst] == ["text", "tool_use", "tool_use"], asst
    assert asst[1]["input"] == {"city": "Paris"}
    print("request wire format: OK (system, merged tool_results, no temperature, tools)")

    # temperature IS forwarded to models that accept it
    async for _ in provider.chat("claude-sonnet-4-6", HISTORY[:2],
                                 params=GenParams(temperature=0.3)):
        pass
    assert captured["body"]["temperature"] == 0.3, "sonnet-4-6 should keep temperature"
    print("sampling guard: OK (kept on 4.6, stripped on 4.8)")

    # --- SSE parsing ---
    text = "".join(e.text for e in events if e.type == "text_delta")
    assert text == "Hello world", text
    assert "SECRET" not in text, "thinking text leaked into output"
    tool_calls = [e.tool_call for e in events if e.type == "tool_call"]
    assert len(tool_calls) == 1 and tool_calls[0].id == "toolu_abc"
    assert tool_calls[0].arguments == {"city": "Paris"}, tool_calls[0].arguments
    usage = [e for e in events if e.type == "usage"]
    assert usage and usage[0].input_tokens == 42 and usage[0].output_tokens == 17
    assert events[-1].type == "done"
    assert not any(e.type == "error" for e in events)
    print("SSE parsing: OK (text, tool assembly, thinking ignored, usage)")

    # --- refusal surfacing ---
    captured["respond_refusal"] = True
    events = [ev async for ev in provider.chat("claude-fable-5",
                                               [Message(role="user", content="hi")])]
    captured.pop("respond_refusal")
    errors = [e for e in events if e.type == "error"]
    assert errors and "declined" in errors[0].message, events
    assert "thinking" not in captured["body"], "fable must not get a thinking param"
    print("refusal surfacing: OK")

    print("\nALL ANTHROPIC PLUGIN TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
