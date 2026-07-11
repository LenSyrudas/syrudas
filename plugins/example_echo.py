"""Example provider plugin.

Any .py file in this folder is scanned at startup; every ModelProvider
subclass with a type_id becomes a selectable provider type in Settings.
Copy this file as a starting point for real adapters (Anthropic, Gemini, ...):
implement list_models() and chat(), translating to/from the normalized
schema in server/schemas.py.
"""
import asyncio

from server.providers.base import ConfigField, ModelProvider
from server.schemas import ModelInfo, StreamEvent


class EchoProvider(ModelProvider):
    type_id = "echo"
    display_name = "Echo (example plugin)"
    config_fields = [
        ConfigField(key="prefix", label="Reply prefix", default="Echo:"),
    ]

    async def list_models(self):
        return [ModelInfo(id="echo-1")]

    async def chat(self, model, messages, tools=None, params=None):
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        reply = f"{self.config.get('prefix') or 'Echo:'} {last_user}"
        for word in reply.split(" "):
            yield StreamEvent(type="text_delta", text=word + " ")
            await asyncio.sleep(0.03)
        yield StreamEvent(type="done")
