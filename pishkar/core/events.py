"""Streaming events for the agent loop.

Modeled on Anthropic's streaming API shape, with `turn_start` / `turn_end`
wrapping the multi-message agent loop and `tool_result` injected after each
tool call. The TypeScript client mirror is generated from these models.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid4())


class _EventBase(BaseModel):
    event_id: str = Field(default_factory=_uuid)
    turn_id: str
    session_id: str
    timestamp: datetime = Field(default_factory=_now)


# --- Content blocks ---------------------------------------------------------


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ThinkingBlock(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str


ContentBlock = Annotated[
    Union[TextBlock, ToolUseBlock, ThinkingBlock],
    Field(discriminator="type"),
]


# --- Deltas -----------------------------------------------------------------


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class InputJsonDelta(BaseModel):
    type: Literal["input_json_delta"] = "input_json_delta"
    partial_json: str


class ThinkingDelta(BaseModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    thinking: str


ContentDelta = Annotated[
    Union[TextDelta, InputJsonDelta, ThinkingDelta],
    Field(discriminator="type"),
]


# --- Events -----------------------------------------------------------------


class TurnStart(_EventBase):
    type: Literal["turn_start"] = "turn_start"
    turn_index: int


class MessageStart(_EventBase):
    type: Literal["message_start"] = "message_start"
    role: Literal["assistant"] = "assistant"
    model: str


class ContentBlockStart(_EventBase):
    type: Literal["content_block_start"] = "content_block_start"
    index: int
    content_block: ContentBlock


class ContentBlockDelta(_EventBase):
    type: Literal["content_block_delta"] = "content_block_delta"
    index: int
    delta: ContentDelta


class ContentBlockStop(_EventBase):
    type: Literal["content_block_stop"] = "content_block_stop"
    index: int


class MessageDelta(_EventBase):
    type: Literal["message_delta"] = "message_delta"
    stop_reason: Literal["end_turn", "tool_use", "max_tokens", "stop_sequence"] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class MessageStop(_EventBase):
    type: Literal["message_stop"] = "message_stop"


class ToolResult(_EventBase):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


class TurnEnd(_EventBase):
    type: Literal["turn_end"] = "turn_end"
    stop_reason: Literal["end_turn", "max_turns", "loop_detected", "error"]


Event = Annotated[
    Union[
        TurnStart,
        MessageStart,
        ContentBlockStart,
        ContentBlockDelta,
        ContentBlockStop,
        MessageDelta,
        MessageStop,
        ToolResult,
        TurnEnd,
    ],
    Field(discriminator="type"),
]
