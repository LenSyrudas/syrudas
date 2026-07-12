"""Builtin agent tools. Each tool declares a JSON-Schema signature and an async run()."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..schemas import ToolSpec


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    requires_approval: bool = False

    async def needs_approval(self, args: dict[str, Any]) -> bool:
        """Per-call approval decision; override for argument-dependent gating."""
        return self.requires_approval

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)

    @abstractmethod
    async def run(self, args: dict[str, Any]) -> str:
        """Execute and return a text result for the model."""


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"


def builtin_tools() -> list[Tool]:
    from .files import FileListTool, FileReadTool, FileWriteTool
    from .memory import MemoryDeleteTool, MemorySaveTool, MemorySearchTool
    from .shell import ShellTool
    from .web import WebFetchTool, WebSearchTool

    return [
        ShellTool(),
        FileReadTool(),
        FileWriteTool(),
        FileListTool(),
        WebFetchTool(),
        WebSearchTool(),
        MemorySaveTool(),
        MemoryDeleteTool(),
        MemorySearchTool(),
    ]
