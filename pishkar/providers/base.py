"""ModelProvider Protocol — the seam every LLM backend implements.

Day-one implementation is `LiteLLMProvider` (Anthropic + OpenAI fallback via
`litellm.Router`). `BudgetedProvider` wraps any `ModelProvider`. Tests use
a hand-rolled fake satisfying the Protocol.

The streaming shape is intentionally narrower than the raw Anthropic SSE
event set — the agent loop translates these chunks into the public
`core.events` types. Keeping the provider surface thin means a new backend
only needs to emit text deltas, tool-call deltas, a stop reason, and final
usage.
"""

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ToolCallDelta(BaseModel):
    """One slice of a streamed tool call.

    `index` groups deltas belonging to the same tool call across chunks
    (OpenAI-style). `id` and `name` arrive on the first chunk; `arguments`
    streams in as accumulating JSON text."""

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str | None = None


class ProviderChunk(BaseModel):
    """One streaming step from a model provider.

    Fields are independent — a chunk may carry text, a tool-call slice, a
    stop reason, or final usage, in any combination. Empty chunks are
    permitted (some providers emit keep-alives)."""

    text: str | None = None
    thinking: str | None = None
    tool_calls: list[ToolCallDelta] = Field(default_factory=list)
    stop_reason: str | None = None
    usage: Usage | None = None
    raw: dict[str, Any] | None = None


@runtime_checkable
class ModelProvider(Protocol):
    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        max_tokens: int | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[ProviderChunk]: ...


__all__ = ["ModelProvider", "ProviderChunk", "ToolCallDelta", "Usage"]
