"""Normalized message / tool / stream-event schema shared by all providers.

Providers translate between this shape and their wire format. The shape is
deliberately OpenAI-flavored since most backends speak that dialect.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    id: str
    name: Optional[str] = None
    # usable context window in tokens, when the backend reports one. None means
    # "not discoverable", not "small" - callers fall back to a fixed budget.
    context_tokens: Optional[int] = None


class ToolSpec(BaseModel):
    """A tool offered to the model. `parameters` is a JSON Schema object."""
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class MediaRef(BaseModel):
    """An image or video attached to a user message.

    The binary lives in the attachments store (server/media.py); a message row
    carries only this metadata. `frames` is the number of sampled frames for a
    video - those frames, not the video itself, are what a model can see."""
    id: str
    name: str
    mime: str
    kind: Literal["image", "video"]
    frames: Optional[int] = None
    ext: Optional[str] = None  # stored file's extension, for locating the bytes


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: Optional[list[ToolCall]] = None  # on assistant messages
    tool_call_id: Optional[str] = None           # on tool-result messages
    attachments: Optional[list[MediaRef]] = None  # on user messages


class StreamEvent(BaseModel):
    """One event in a provider's chat stream.

    type            payload fields
    text_delta      text
    tool_call       tool_call
    usage           input_tokens, output_tokens
    error           message
    done            (nothing)
    """
    type: Literal["text_delta", "tool_call", "usage", "error", "done"]
    text: Optional[str] = None
    tool_call: Optional[ToolCall] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    message: Optional[str] = None


class GenParams(BaseModel):
    """Generation parameters common enough to normalize; providers may ignore."""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
