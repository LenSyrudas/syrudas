"""The provider plugin contract.

A provider plugin is a subclass of ModelProvider in server/providers/ (builtin)
or in the top-level plugins/ directory (user-added). The registry discovers it
by its `type_id`. Users then create configured *instances* of a provider type
in the settings UI (e.g. "Ollama local" with base_url http://localhost:11434/v1).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, ClassVar, Literal, Optional

from pydantic import BaseModel

from ..schemas import GenParams, Message, ModelInfo, StreamEvent, ToolSpec


class ConfigField(BaseModel):
    """Declares one config input the settings UI should render for this type."""
    key: str
    label: str
    type: Literal["text", "password", "url"] = "text"
    required: bool = False
    default: str = ""
    placeholder: str = ""


class ModelProvider(ABC):
    type_id: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    config_fields: ClassVar[list[ConfigField]] = []

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Models this configured instance can serve."""

    @abstractmethod
    def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        params: Optional[GenParams] = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion as StreamEvents. Must end with a `done` event
        (or `error`). Tool calls are emitted as complete `tool_call` events."""

    async def check(self) -> str:
        """Connection test for the settings UI. Returns a human summary or raises."""
        models = await self.list_models()
        return f"OK - {len(models)} models available"
