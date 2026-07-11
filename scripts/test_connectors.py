"""Wire-protocol tests for the Anthropic and Gemini plugins.

Uses httpx.MockTransport via the plugins' `_transport` config hook - no
network, no API keys. Asserts both directions: the exact request bodies the
adapters produce, and correct parsing of protocol-faithful SSE responses.

Run: .venv\\Scripts\\python.exe scripts\\test_connectors.py
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.schemas import GenParams, Message, ToolCall, ToolSpec  # noqa: E402


def load_plugin(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "plugins" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


anthropic = load_plugin("anthropic")
gemini = load_plugin("gemini")

HISTORY = [
    Message(role="system", content="You are terse."),
    Message(role="user", content="What's the weather in Paris and Lyon?"),
    Message(role="assistant", content="Checking both.", tool_calls=[
        ToolCall(id="call_1", name="get_weather", arguments={"city": "Paris"}),
        ToolCall(id="call_2", name="get_weather", arguments={"city": "Lyon"}),
    ]),
    Message(role="tool", content="Paris: 21C", tool_call_id="call_1"),
    Message(role="tool", content="Lyon: 24C", tool_call_id="call_2"),
    Message(role="user", content="Which is warmer?"),
]
TOOLS = [ToolSpec(name="get_weather", description="Get weather",
                  parameters={"type": "object",
                              "properties": {"city": {"type": "string"}},
                              "required": ["city"],
                              "additionalProperties": False})]


def sse(events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


async def collect(gen):
    return [ev async for ev in gen]


# ---------------- Anthropic ----------------

ANTHROPIC_SSE = sse([
    {"type": "message_start", "message": {"usage": {"input_tokens": 42}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Lyon"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " is warmer."}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1,
     "content_block": {"type": "tool_use", "id": "toolu_9", "name": "get_weather", "input": {}}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": '{"city": "'}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": 'Nice"}'}},
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 17}},
    {"type": "message_stop"},
])


async def test_anthropic():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-api-key"] == "sk-test"
        assert request.headers["anthropic-version"] == "2023-06-01"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"}]})
        body = json.loads(request.content)
        # system extracted to top level
        assert body["system"] == "You are terse."
        assert body["max_tokens"] > 0
        # sampling params must NOT reach opus-4-8
        assert "temperature" not in body, "temperature leaked to a no-sampling model"
        # tools present + thinking disabled for replay safety
        assert body["tools"][0]["name"] == "get_weather"
        assert body["thinking"] == {"type": "disabled"}
        msgs = body["messages"]
        # assistant turn carries both tool_use blocks
        tu = [b for b in msgs[1]["content"] if b["type"] == "tool_use"]
        assert [b["id"] for b in tu] == ["call_1", "call_2"], tu
        # BOTH tool results merged into ONE user message
        tr_msg = msgs[2]
        assert tr_msg["role"] == "user"
        assert [b["type"] for b in tr_msg["content"]] == ["tool_result", "tool_result"], tr_msg
        assert msgs[3]["content"] == "Which is warmer?"
        return httpx.Response(200, content=ANTHROPIC_SSE,
                              headers={"content-type": "text/event-stream"})

    provider = anthropic.AnthropicProvider({
        "api_key": "sk-test", "_transport": httpx.MockTransport(handler)})

    models = await provider.list_models()
    assert [m.id for m in models] == ["claude-opus-4-8"]

    events = await collect(provider.chat(
        "claude-opus-4-8", HISTORY, tools=TOOLS, params=GenParams(temperature=0.5)))
    text = "".join(e.text for e in events if e.type == "text_delta")
    assert text == "Lyon is warmer.", text
    calls = [e.tool_call for e in events if e.type == "tool_call"]
    assert len(calls) == 1 and calls[0].name == "get_weather"
    assert calls[0].arguments == {"city": "Nice"}, calls[0].arguments
    usage = next(e for e in events if e.type == "usage")
    assert usage.input_tokens == 42 and usage.output_tokens == 17
    assert events[-1].type == "done"
    print("anthropic: models, request shape, SSE text/tools/usage OK")


async def test_anthropic_sampling_retry():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "temperature" in body:
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "temperature is not supported on this model"}})
        return httpx.Response(200, content=sse([
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "ok"}},
            {"type": "message_stop"},
        ]), headers={"content-type": "text/event-stream"})

    provider = anthropic.AnthropicProvider({
        "api_key": "sk-test", "_transport": httpx.MockTransport(handler)})
    # a future model id unknown to the marker list -> temperature is sent,
    # 400s, and the adapter must retry without it
    events = await collect(provider.chat(
        "claude-hypothetical-9", [Message(role="user", content="hi")],
        params=GenParams(temperature=0.7)))
    assert len(calls) == 2 and "temperature" not in calls[1]
    assert not any(e.type == "error" for e in events), events
    assert "".join(e.text for e in events if e.type == "text_delta") == "ok"
    print("anthropic: sampling-param 400 retry OK")


async def test_anthropic_refusal():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse([
            {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
            {"type": "message_delta", "delta": {"stop_reason": "refusal"},
             "usage": {"output_tokens": 0}},
            {"type": "message_stop"},
        ]), headers={"content-type": "text/event-stream"})

    provider = anthropic.AnthropicProvider({
        "api_key": "sk-test", "_transport": httpx.MockTransport(handler)})
    events = await collect(provider.chat(
        "claude-opus-4-8", [Message(role="user", content="hi")]))
    assert any(e.type == "error" and "declined" in e.message for e in events), events
    print("anthropic: refusal surfaced OK")


# ---------------- Gemini ----------------

GEMINI_SSE = sse([
    {"candidates": [{"content": {"role": "model",
                                 "parts": [{"text": "Lyon is "}]}}]},
    {"candidates": [{"content": {"role": "model",
                                 "parts": [{"text": "warmer."},
                                           {"functionCall": {"name": "get_weather",
                                                             "args": {"city": "Nice"}}}]},
                     "finishReason": "STOP"}],
     "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 11}},
])


async def test_gemini():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "AIza-test"
        if request.url.path.endswith("/models") and request.method == "GET":
            return httpx.Response(200, json={"models": [
                {"name": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro",
                 "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embedding-001",
                 "supportedGenerationMethods": ["embedContent"]},
            ]})
        assert "streamGenerateContent" in request.url.path
        assert request.url.params["alt"] == "sse"
        body = json.loads(request.content)
        assert body["systemInstruction"]["parts"][0]["text"] == "You are terse."
        # schema cleanup: additionalProperties must be stripped
        decl = body["tools"][0]["functionDeclarations"][0]
        assert "additionalProperties" not in json.dumps(decl)
        contents = body["contents"]
        roles = [c["role"] for c in contents]
        assert roles == ["user", "model", "user", "user"] or roles == ["user", "model", "user"], roles
        # model turn: text part + two functionCall parts
        model_parts = contents[1]["parts"]
        fc = [p for p in model_parts if "functionCall" in p]
        assert len(fc) == 2 and fc[0]["functionCall"]["name"] == "get_weather"
        # tool results: functionResponse parts with the mapped NAME
        fr = [p for c in contents[2:] for p in c["parts"] if "functionResponse" in p]
        assert len(fr) == 2, contents
        assert all(p["functionResponse"]["name"] == "get_weather" for p in fr)
        assert body["generationConfig"]["temperature"] == 0.5
        return httpx.Response(200, content=GEMINI_SSE,
                              headers={"content-type": "text/event-stream"})

    provider = gemini.GeminiProvider({
        "api_key": "AIza-test", "_transport": httpx.MockTransport(handler)})

    models = await provider.list_models()
    assert [m.id for m in models] == ["gemini-2.5-pro"], models

    events = await collect(provider.chat(
        "gemini-2.5-pro", HISTORY, tools=TOOLS, params=GenParams(temperature=0.5)))
    text = "".join(e.text for e in events if e.type == "text_delta")
    assert text == "Lyon is warmer.", text
    calls = [e.tool_call for e in events if e.type == "tool_call"]
    assert len(calls) == 1 and calls[0].id.startswith("gem_")
    assert calls[0].arguments == {"city": "Nice"}
    usage = next(e for e in events if e.type == "usage")
    assert usage.input_tokens == 30 and usage.output_tokens == 11
    assert events[-1].type == "done"
    print("gemini: models filter, request shape, SSE text/tools/usage OK")


async def test_gemini_safety():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse([
            {"candidates": [{"finishReason": "SAFETY"}],
             "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 0}},
        ]), headers={"content-type": "text/event-stream"})

    provider = gemini.GeminiProvider({
        "api_key": "AIza-test", "_transport": httpx.MockTransport(handler)})
    events = await collect(provider.chat(
        "gemini-2.5-pro", [Message(role="user", content="hi")]))
    assert any(e.type == "error" and "declined" in e.message for e in events), events
    print("gemini: safety refusal surfaced OK")


async def main():
    await test_anthropic()
    await test_anthropic_sampling_retry()
    await test_anthropic_refusal()
    await test_gemini()
    await test_gemini_safety()
    print("\nALL CONNECTOR TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
