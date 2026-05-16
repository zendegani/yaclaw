"""LangFuse sink — sends turn-level traces to a self-hosted LangFuse instance.

LangFuse exposes both a LiteLLM callback and a direct SDK. We use the
direct SDK because the hook payloads already carry everything LangFuse
needs (turn_id, model, token usage). The `langfuse` SDK is lazy-imported
so the rest of the runtime stays usable when LangFuse isn't installed.

All writes go through `HookManager.emit`, so failures and slowness
inside the LangFuse client never block the agent loop.
"""

from typing import Any

from pishkar.gateway.hooks import AFTER_LLM, ON_TURN_COMPLETE, HookManager


class LangFuseSink:
    """Thin wrapper that forwards `after_llm` and `on_turn_complete`
    payloads to a LangFuse client.

    Pass any object exposing `.trace(...)` and `.generation(...)` —
    typically `langfuse.Langfuse(...)`. Tests inject a fake."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._traces: dict[str, Any] = {}

    def attach(self, hooks: HookManager) -> None:
        hooks.on(AFTER_LLM, self._on_after_llm)
        hooks.on(ON_TURN_COMPLETE, self._on_turn_complete)

    async def _on_after_llm(
        self,
        *,
        turn_id: str,
        session_id: str | None = None,
        user_id: str | None = None,
        model: str = "unknown",
        stop_reason: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        **_: Any,
    ) -> None:
        trace = self._traces.get(turn_id)
        if trace is None:
            trace = self._client.trace(
                id=turn_id, session_id=session_id, user_id=user_id
            )
            self._traces[turn_id] = trace
        trace.generation(
            name=model,
            model=model,
            usage={"input": input_tokens, "output": output_tokens},
            metadata={"stop_reason": stop_reason},
        )

    async def _on_turn_complete(
        self,
        *,
        turn_id: str,
        stop_reason: str | None = None,
        **_: Any,
    ) -> None:
        trace = self._traces.pop(turn_id, None)
        if trace is None:
            return
        if hasattr(trace, "update"):
            trace.update(metadata={"final_stop_reason": stop_reason})


def build_langfuse_client(
    public_key: str,
    secret_key: str,
    host: str = "http://localhost:3000",
) -> Any:
    """Lazy-import factory for `langfuse.Langfuse`."""
    # No specific code: langfuse is an optional extra. When it's
    # installed the error is `import-untyped` (no py.typed marker);
    # when it's not installed the error is `import-not-found`. Plain
    # `# type: ignore` covers both without going stale.
    from langfuse import Langfuse  # type: ignore

    return Langfuse(public_key=public_key, secret_key=secret_key, host=host)


__all__ = ["LangFuseSink", "build_langfuse_client"]
