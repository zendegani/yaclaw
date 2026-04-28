"""Tool-aware history compaction.

When the OpenAI-shaped message list grows past `max_messages`, drop the
*oldest* assistant prose turns (assistant messages with no `tool_calls`)
first. Tool-call/tool-result *pairs* are preserved together — orphaning a
`tool` message without its `assistant` `tool_calls` precursor breaks the
provider's invariants.

System messages are never dropped. The most recent user message is never
dropped. The compactor returns a new list; the caller assigns it back.
"""

from typing import Any


def _is_system(m: dict[str, Any]) -> bool:
    return m.get("role") == "system"


def _is_assistant_prose(m: dict[str, Any]) -> bool:
    return m.get("role") == "assistant" and not m.get("tool_calls")


def compact(messages: list[dict[str, Any]], *, max_messages: int = 40) -> list[dict[str, Any]]:
    if len(messages) <= max_messages:
        return list(messages)

    keep_last_user = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )

    out = list(messages)
    i = 0
    while len(out) > max_messages and i < len(out):
        m = out[i]
        if _is_system(m) or i == keep_last_user:
            i += 1
            continue
        if _is_assistant_prose(m):
            out.pop(i)
            if keep_last_user is not None and i < keep_last_user:
                keep_last_user -= 1
            continue
        i += 1

    if len(out) <= max_messages:
        return out

    # Second pass: drop oldest tool_call / tool pairs together. Find an
    # assistant with tool_calls and the contiguous run of tool messages
    # following it; remove the whole block.
    i = 0
    while len(out) > max_messages and i < len(out):
        m = out[i]
        if _is_system(m) or i == keep_last_user:
            i += 1
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            j = i + 1
            while j < len(out) and out[j].get("role") == "tool":
                j += 1
            removed = j - i
            del out[i:j]
            if keep_last_user is not None and i < keep_last_user:
                keep_last_user -= removed
            continue
        i += 1

    return out


__all__ = ["compact"]
