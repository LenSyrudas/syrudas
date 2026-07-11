"""Smoke test: stream a completion from local Ollama through the provider plugin.

Run: .venv\\Scripts\\python.exe scripts\\smoke_provider.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.providers.registry import create_provider, provider_types
from server.schemas import GenParams, Message, ToolSpec


async def main() -> int:
    print("Provider types discovered:", [t["type_id"] for t in provider_types()])

    provider = create_provider("openai_compat", {"base_url": "http://localhost:11434/v1"})
    models = await provider.list_models()
    print(f"list_models: {len(models)} models ->", [m.id for m in models][:8])

    print("\n--- streaming chat (llama3.1:8b) ---")
    chunks = []
    async for ev in provider.chat(
        "llama3.1:8b",
        [Message(role="user", content="Reply with exactly: ARGOS ONLINE")],
        params=GenParams(temperature=0.0),
    ):
        if ev.type == "text_delta":
            chunks.append(ev.text)
            print(ev.text, end="", flush=True)
        elif ev.type == "error":
            print("ERROR:", ev.message)
            return 1
    text = "".join(chunks)
    assert text.strip(), "no streamed text received"

    print("\n\n--- tool-call test ---")
    tool = ToolSpec(
        name="get_weather",
        description="Get current weather for a city",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    got_tool_call = False
    async for ev in provider.chat(
        "llama3.1:8b",
        [Message(role="user", content="What's the weather in Paris? Use the tool.")],
        tools=[tool],
        params=GenParams(temperature=0.0),
    ):
        if ev.type == "tool_call":
            got_tool_call = True
            print("tool_call:", ev.tool_call.name, ev.tool_call.arguments)
        elif ev.type == "error":
            print("ERROR:", ev.message)
            return 1
    assert got_tool_call, "model did not emit a tool call"
    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
