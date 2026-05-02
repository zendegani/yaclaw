"""Observability sinks.

Two destinations fan out from the Hooks layer:

* `SqliteSink` — always-on append-only audit (cannot be lost).
* A pluggable LLM-trace backend — Arize Phoenix by default (Pi 5 / SBC
  friendly), LangFuse as an opt-in alternative for VPS hosts with the
  RAM headroom. Selected via `PISHKAR_TRACE_BACKEND` (`phoenix` /
  `langfuse` / `none`).

Both trace sinks share the same Hook contract, so swapping is a
one-line config change — the agent loop is unaware which is wired.
"""

from pishkar.observability.langfuse_sink import LangFuseSink, build_langfuse_client
from pishkar.observability.phoenix_sink import PhoenixSink, build_phoenix_tracer
from pishkar.observability.sqlite_sink import SqliteSink

__all__ = [
    "LangFuseSink",
    "PhoenixSink",
    "SqliteSink",
    "build_langfuse_client",
    "build_phoenix_tracer",
]
