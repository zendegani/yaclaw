"""Agent loop — `run_turn` async generator yielding streaming events.

Each call to `run_turn` consumes one inbound user message and drives the
provider+tool dance until the model either (a) emits an `end_turn` with
no tool calls, (b) trips the loop guard, or (c) hits the max-turn budget.
The generator yields the typed events from `core.events`; callers fan
them out to channels and observability sinks.

The loop is hand-rolled (not delegated to an SDK) so primitives like the
loop guard, max-turn budget, tool-aware compaction, and approval gate
can be inserted directly between provider chunks and tool dispatches.
"""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from pishkar.core.compaction import compact
from pishkar.core.context import current_session_id, current_turn_id
from pishkar.core.events import (
    ContentBlockDelta,
    ContentBlockStart,
    ContentBlockStop,
    Event,
    InputJsonDelta,
    MessageDelta,
    MessageStart,
    MessageStop,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolResult as ToolResultEvent,
    ToolUseBlock,
    TurnEnd,
    TurnStart,
)
from pishkar.core.loop_guard import LoopGuard
from pishkar.core.messages import InboundMessage
from pishkar.gateway.hooks import AFTER_LLM, ON_TURN_COMPLETE, HookManager
from pishkar.providers.base import ModelProvider
from pishkar.tools.runner import ToolRunner

DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_MESSAGES = 40


def _safe_parse_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


class _ToolCallBuilder:
    def __init__(self) -> None:
        self.id: str = ""
        self.name: str = ""
        self.args_json: str = ""
        self.block_index: int = -1

    def update(self, *, id: str | None, name: str | None, arguments: str | None) -> None:
        if id and not self.id:
            self.id = id
        if name and not self.name:
            self.name = name
        if arguments:
            self.args_json += arguments

    def parsed_input(self) -> dict[str, Any]:
        return _safe_parse_json(self.args_json)


def _normalize_stop_reason(raw: str | None, *, has_tools: bool) -> str | None:
    if raw is None:
        return "tool_use" if has_tools else "end_turn"
    if raw in {"tool_calls", "tool_use"}:
        return "tool_use"
    if raw in {"length", "max_tokens"}:
        return "max_tokens"
    if raw == "stop_sequence":
        return "stop_sequence"
    return "end_turn"


async def run_turn(
    *,
    user_message: InboundMessage,
    history: list[dict[str, Any]],
    provider: ModelProvider,
    runner: ToolRunner,
    tool_schemas: list[dict[str, Any]] | None = None,
    system: str | None = None,
    model: str = "claude-opus-4-7",
    turn_id: str | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    loop_guard: LoopGuard | None = None,
    hooks: HookManager | None = None,
) -> AsyncIterator[Event]:
    """Drive one turn-of-conversation. Mutates `history` in place."""

    turn_id = turn_id or str(uuid4())
    session_id = user_message.session_id
    current_turn_id.set(turn_id)
    current_session_id.set(session_id)
    history.append({"role": "user", "content": user_message.content})

    yield TurnStart(turn_id=turn_id, session_id=session_id, turn_index=0)

    for _ in range(max_turns):
        history[:] = compact(history, max_messages=max_messages)

        yield MessageStart(turn_id=turn_id, session_id=session_id, model=model)

        text_block_index: int | None = None
        thinking_block_index: int | None = None
        text_buf: list[str] = []
        builders: dict[int, _ToolCallBuilder] = {}
        next_block_index = 0
        stop_reason: str | None = None
        usage_in = 0
        usage_out = 0

        async for chunk in provider.stream(
            model=model,
            messages=history,
            tools=tool_schemas or None,
            system=system,
            user_id=user_message.user_id,
        ):
            if chunk.thinking:
                if thinking_block_index is None:
                    thinking_block_index = next_block_index
                    next_block_index += 1
                    yield ContentBlockStart(
                        turn_id=turn_id,
                        session_id=session_id,
                        index=thinking_block_index,
                        content_block=ThinkingBlock(thinking=""),
                    )
                yield ContentBlockDelta(
                    turn_id=turn_id,
                    session_id=session_id,
                    index=thinking_block_index,
                    delta=ThinkingDelta(thinking=chunk.thinking),
                )

            if chunk.text:
                if text_block_index is None:
                    text_block_index = next_block_index
                    next_block_index += 1
                    yield ContentBlockStart(
                        turn_id=turn_id,
                        session_id=session_id,
                        index=text_block_index,
                        content_block=TextBlock(text=""),
                    )
                yield ContentBlockDelta(
                    turn_id=turn_id,
                    session_id=session_id,
                    index=text_block_index,
                    delta=TextDelta(text=chunk.text),
                )
                text_buf.append(chunk.text)

            for tc in chunk.tool_calls:
                builder = builders.get(tc.index)
                if builder is None:
                    builder = _ToolCallBuilder()
                    builder.block_index = next_block_index
                    next_block_index += 1
                    builders[tc.index] = builder
                    yield ContentBlockStart(
                        turn_id=turn_id,
                        session_id=session_id,
                        index=builder.block_index,
                        content_block=ToolUseBlock(
                            id=tc.id or "", name=tc.name or "", input={}
                        ),
                    )
                builder.update(id=tc.id, name=tc.name, arguments=tc.arguments)
                if tc.arguments:
                    yield ContentBlockDelta(
                        turn_id=turn_id,
                        session_id=session_id,
                        index=builder.block_index,
                        delta=InputJsonDelta(partial_json=tc.arguments),
                    )

            if chunk.stop_reason:
                stop_reason = chunk.stop_reason
            if chunk.usage:
                usage_in = chunk.usage.input_tokens
                usage_out = chunk.usage.output_tokens

        if thinking_block_index is not None:
            yield ContentBlockStop(
                turn_id=turn_id, session_id=session_id, index=thinking_block_index
            )
        if text_block_index is not None:
            yield ContentBlockStop(
                turn_id=turn_id, session_id=session_id, index=text_block_index
            )
        for builder in sorted(builders.values(), key=lambda b: b.block_index):
            yield ContentBlockStop(
                turn_id=turn_id, session_id=session_id, index=builder.block_index
            )

        normalized = _normalize_stop_reason(stop_reason, has_tools=bool(builders))
        yield MessageDelta(
            turn_id=turn_id,
            session_id=session_id,
            stop_reason=normalized,
            input_tokens=usage_in or None,
            output_tokens=usage_out or None,
        )
        yield MessageStop(turn_id=turn_id, session_id=session_id)

        assistant_text = "".join(text_buf)
        tool_call_summaries = [
            {
                "id": b.id or f"call_{i}",
                "name": b.name,
                "arguments": b.args_json or "{}",
            }
            for i, b in sorted(builders.items())
        ]

        if hooks is not None:
            hooks.emit(
                AFTER_LLM,
                turn_id=turn_id,
                session_id=session_id,
                user_id=user_message.user_id,
                model=model,
                stop_reason=normalized,
                input_tokens=usage_in,
                output_tokens=usage_out,
                messages=[dict(m) for m in history],
                system=system,
                assistant_text=assistant_text,
                tool_calls=tool_call_summaries,
            )

        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if text_buf:
            assistant_msg["content"] = assistant_text
        if builders:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
                for tc in tool_call_summaries
            ]
        history.append(assistant_msg)

        if not builders:
            if hooks is not None:
                hooks.emit(
                    ON_TURN_COMPLETE,
                    turn_id=turn_id, session_id=session_id, stop_reason="end_turn",
                )
            yield TurnEnd(turn_id=turn_id, session_id=session_id, stop_reason="end_turn")
            return

        for _, builder in sorted(builders.items()):
            input_dict = builder.parsed_input()
            if loop_guard is not None:
                if loop_guard.is_looping(builder.name, input_dict):
                    if hooks is not None:
                        hooks.emit(
                            ON_TURN_COMPLETE,
                            turn_id=turn_id, session_id=session_id,
                            stop_reason="loop_detected",
                        )
                    yield TurnEnd(
                        turn_id=turn_id,
                        session_id=session_id,
                        stop_reason="loop_detected",
                    )
                    return
                loop_guard.record(builder.name, input_dict)

            tool_use_id = builder.id or f"call_{builder.block_index}"
            tool_result = await runner.run(builder.name, input_dict)
            yield ToolResultEvent(
                turn_id=turn_id,
                session_id=session_id,
                tool_use_id=tool_use_id,
                content=tool_result.content,
                is_error=tool_result.is_error,
            )
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_use_id,
                    "content": tool_result.content,
                }
            )

    if hooks is not None:
        hooks.emit(
            ON_TURN_COMPLETE,
            turn_id=turn_id, session_id=session_id, stop_reason="max_turns",
        )
    yield TurnEnd(turn_id=turn_id, session_id=session_id, stop_reason="max_turns")


__all__ = ["DEFAULT_MAX_TURNS", "run_turn"]
