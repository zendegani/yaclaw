# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This repo (`yaclaw`) is a **design-stage** project for **Pishkar**, a personal AI butler / agent runtime. There is no source code yet — only architectural design documents. `pyproject.toml` declares the project name and Python ≥ 3.14 with no dependencies. `README.md` is empty.

When asked to implement something, expect to be creating files from scratch under the layout described in `design.md`. Do not assume any module, class, or test exists until verified.

## The three design documents

These three files are the source of truth. They are long, internally cross-referenced, and intended to be read together. Always consult them before proposing implementation choices.

- **`architecture.md`** — Comparative analysis of 15 reference agent runtimes ("Claws") across ~20 architectural concerns (execution pattern, agent loop, memory, channels, safety, providers, multi-user identity, triggers, sandboxing, skills, observability, etc.). For each concern it surveys the field, lists trade-offs, and states **Pishkar's adopted approach** with reasoning. This is the "why" document.
- **`design.md`** — Implementation companion: hexagonal architecture diagram, the concrete `pishkar/` Python module layout, a numbered 22-step day-one build plan, the safety-minima checklist (17 items), explicitly deferred features whose seams are pre-paid, v2 patterns to revisit, and footnotes/caveats. This is the "what to build" document.
- **`claws.md`** — Single wide comparison table of every reference project against every architectural feature, with Pishkar in the leftmost column. Use for quick lookup of how a given Claw handles a given concern.

## Load-bearing architectural decisions

Pulled forward so you can apply them without re-reading the full docs. When in doubt, check `architecture.md` for the reasoning.

- **Execution pattern: hybrid.** Deterministic Gateway (channel routing, queueing, hooks) wraps a hand-rolled autonomous agent loop. Same runtime serves conversational and delegated modes.
- **Agent loop: hand-rolled `run_turn`.** Not an SDK. Hand-rolling is a precondition for in-loop primitives (Hooks, Steering, SubTurn, approval gate, budget enforcement, SHA256 loop detection, tool-aware compaction). Max-turn budget = 10.
- **Memory: workspace markdown + SQLite session log.** `~/.pishkar/users/<user_id>/{SOUL,AGENTS,USER,HEARTBEAT}.md` plus an append-only SQLite DB. Typed memory categories and `sqlite-vec` semantic recall are deferred but not blocked.
- **Channels: FastAPI + WebSocket** with a typed event protocol. The "CLI" is a TUI client over the same WebSocket — not a separate channel implementation.
- **Provider: LiteLLM library + `Router`** (Anthropic primary, OpenAI fallback). Wrapped by `BudgetedProvider` (auto-concise at 70% of daily token budget per `user_id`).
- **Safety: approval gate + token budget + loop guard + per-tool timeout + max-result cap + atomic workspace writes + fail-open hooks.** See `design.md` "Safety minima" table (17 items) — these are cheap on day one and expensive to retrofit.
- **Observability: local SQLite append-only log + LangFuse self-hosted (Docker).** Hook emission is fail-open via `asyncio.create_task` with swallowed exceptions — never block the loop on the exporter.
- **Reserve seams day one:** `InboundMessage.user_id`, `InboundMessage.trust_level`, `InboundMessage.metadata`, `OutboundMessage.metadata`, `ToolRunner(Protocol)`, `Channel(Protocol)`, `TriggerSource(Protocol)`, `ModelProvider(Protocol)`, `BackupBackend(Protocol)`. These let later additions (Telegram, Docker sandbox, semantic memory, S3 backup, …) land without rearchitecting.
- **Pydantic → TypeScript codegen** for the event protocol on day one (`datamodel-code-generator` or `pydantic2ts`). Schema drift between Python and TS is a known silent failure mode.
- **One stable entrypoint:** `python -m pishkar.server`. The OS-native daemon (LaunchAgent / systemd / Task Scheduler) is a thin wrapper around the same command — no second entrypoint.

## Explicitly out of scope for day one

Do not propose these unless asked: custom plugin manifest (MCP covers it), DAG workflow engine, Docker-per-session sandboxing, vector database, multi-agent routing, RL skill self-improvement, dashboard/settings UI (the workspace IS the settings UI), OpenTelemetry. See `design.md` "Out of scope for day one" for the reasoning on each.

## Working in this repo

- Python target is **3.14**. No dependencies are pinned yet — when adding the first ones, follow the day-one build plan order in `design.md` (section "Day-one build plan", items 1–22).
- The design docs use prose-heavy markdown with intentional cross-references between sections (e.g. "§5", "item 11"). Preserve this style if editing them.
- When implementing something from the design docs, cite the section/item it comes from in the PR description so future readers can trace decision → code.
