# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This repo (`yaclaw`) is a **design-stage** project for **Pishkar**, a personal AI butler / agent runtime. There is no source code yet — only architectural design documents. `pyproject.toml` declares the project name and Python ≥ 3.14 with no dependencies. `README.md` is empty.

When asked to implement something, expect to be creating files from scratch under the layout described in `docs/design.md`. Do not assume any module, class, or test exists until verified.

## The design documents

- **`docs/design.md`** — **Primary reference.** Follow this when building. Contains the hexagonal architecture diagram, the concrete `pishkar/` Python module layout, the numbered 22-step day-one build plan, the safety-minima checklist (17 items), explicitly deferred features whose seams are pre-paid, v2 patterns to revisit, and footnotes/caveats.
- **`docs/architecture.md`** — **Consult when in doubt.** The "why" document: comparative analysis of 15 reference agent runtimes across ~20 architectural concerns, with Pishkar's adopted approach and reasoning for each. Read the relevant section when a `docs/design.md` decision is unclear or you need to understand the trade-offs behind it.
- **`docs/claws.md`** — **Do not read.** This is the user's personal reference matrix (15 reference projects × every feature). Not needed for implementation.

## Load-bearing architectural decisions

Pulled forward so you can apply them without re-reading the full docs. When in doubt, check `docs/architecture.md` for the reasoning.

- **Execution pattern: hybrid.** Deterministic Gateway (channel routing, queueing, hooks) wraps a hand-rolled autonomous agent loop. Same runtime serves conversational and delegated modes.
- **Agent loop: hand-rolled `run_turn`.** Not an SDK. Hand-rolling is a precondition for in-loop primitives (Hooks, Steering, SubTurn, approval gate, budget enforcement, SHA256 loop detection, tool-aware compaction). Max-turn budget = 10.
- **Memory: workspace markdown + SQLite session log.** `~/.pishkar/users/<user_id>/{SOUL,AGENTS,USER,HEARTBEAT}.md` plus an append-only SQLite DB. Typed memory categories and `sqlite-vec` semantic recall are deferred but not blocked.
- **Channels: FastAPI + WebSocket** with a typed event protocol. The "CLI" is a TUI client over the same WebSocket — not a separate channel implementation.
- **Provider: LiteLLM library + `Router`** (Anthropic primary, OpenAI fallback). Wrapped by `BudgetedProvider` (auto-concise at 70% of daily token budget per `user_id`).
- **Safety: approval gate + token budget + loop guard + per-tool timeout + max-result cap + atomic workspace writes + fail-open hooks.** See `docs/design.md` "Safety minima" table (17 items) — these are cheap on day one and expensive to retrofit.
- **Observability: local SQLite append-only log + LangFuse self-hosted (Docker).** Hook emission is fail-open via `asyncio.create_task` with swallowed exceptions — never block the loop on the exporter.
- **Reserve seams day one:** `InboundMessage.user_id`, `InboundMessage.trust_level`, `InboundMessage.metadata`, `OutboundMessage.metadata`, `ToolRunner(Protocol)`, `Channel(Protocol)`, `TriggerSource(Protocol)`, `ModelProvider(Protocol)`, `BackupBackend(Protocol)`. These let later additions (Telegram, Docker sandbox, semantic memory, S3 backup, …) land without rearchitecting.
- **Pydantic → TypeScript codegen** for the event protocol on day one (`datamodel-code-generator` or `pydantic2ts`). Schema drift between Python and TS is a known silent failure mode.
- **One stable entrypoint:** `python -m pishkar.server`. The OS-native daemon (LaunchAgent / systemd / Task Scheduler) is a thin wrapper around the same command — no second entrypoint.

## Explicitly out of scope for day one

Do not propose these unless asked: custom plugin manifest (MCP covers it), DAG workflow engine, Docker-per-session sandboxing, vector database, multi-agent routing, RL skill self-improvement, dashboard/settings UI (the workspace IS the settings UI), OpenTelemetry. See `docs/design.md` "Out of scope for day one" for the reasoning on each.

## Working in this repo

- Python target is **3.14**. No dependencies are pinned yet — when adding the first ones, follow the day-one build plan order in `docs/design.md` (section "Day-one build plan", items 1–22).
- The design docs use prose-heavy markdown with intentional cross-references between sections (e.g. "§5", "item 11"). Preserve this style if editing them.
- When implementing something from the design docs, cite the section/item it comes from in the PR description so future readers can trace decision → code.
