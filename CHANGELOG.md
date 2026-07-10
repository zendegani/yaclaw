# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Lowered the minimum supported Python from 3.14 to 3.12. Self-referencing
  annotations that relied on 3.14's deferred evaluation (PEP 649) now use
  `typing.Self` or quoted forward references.

## [0.1.0] - 2026-07-10

First release (alpha). Pishkar is a personal AI butler runtime: local-first,
safety-gated, lightweight enough for a Raspberry Pi 5.

### Added

- **Core agent loop** — hand-rolled turn loop with typed streaming events
  (mirroring the Anthropic API shape), max-turn budget, SHA-256 loop guard,
  and tool-aware context compaction.
- **Gateway** — SQLite-backed inbound queue with per-session serial workers,
  mid-turn crash detection and recovery on restart, fail-open hooks
  (`before_tool`, `on_tool_result`, `after_llm`, `on_turn_complete`),
  approval routing, and cross-channel fan-out.
- **Multi-provider LLM** — LiteLLM-based provider supporting Anthropic,
  OpenAI, Gemini, OpenRouter, Groq, Moonshot, Qwen, and MiniMax; fallback
  model chain via `PISHKAR_MODEL_1..N`; daily token budget
  (`PISHKAR_DAILY_TOKEN_BUDGET`) with auto-concise mode at 70% and
  threshold alerts delivered as chat messages.
- **Tools & safety** — `@tool` registry, subprocess runner with per-tool
  timeout and result cap, approval gate (Ask / Allow Once / Allow Session),
  MCP bridge (stdio + HTTP), and native tools: bash, fs, http, read_url,
  search, plan, speak.
- **Trust levels** — every inbound message carries a `trust_level` that
  filters which tools the agent may use; webhook messages are untrusted by
  default.
- **Channels** — React + Vite Web UI over WebSocket (with `last_event_id`
  replay), owner-only Telegram bot (inline-keyboard approvals, voice notes,
  `/model` switching), and CLI.
- **Voice** — speech-to-text via Groq Whisper, optional text-to-speech via
  local Piper.
- **Triggers** — heartbeat cron (`cron.json` + `HEARTBEAT.md`, with
  catch-up after downtime) and webhook trigger (`POST /webhook/{name}`,
  per-hook secret and trust level, hot-reloaded from `webhooks.json`).
- **Memory & workspace** — markdown workspace under
  `~/.pishkar/users/<user_id>/` (`SOUL.md`, `USER.md`, `AGENTS.md`,
  `HEARTBEAT.md`, skills), append-only SQLite session store, opt-in
  Reflector that distills `MEMORY.md`, and optional semantic recall
  (`PISHKAR_EMBEDDING_MODEL` + `search_memory` tool, sqlite-vec
  accelerated with a pure-Python fallback).
- **Backups** — `backup` tool producing a timestamped archive of a
  consistent `sessions.db` snapshot (SQLite online backup API) plus the
  workspace tree, written to `PISHKAR_BACKUP_DIR` behind a `BackupBackend`
  seam for future S3/B2 backends.
- **Observability** — always-on SQLite audit log plus a pluggable trace
  backend (`PISHKAR_TRACE_BACKEND=phoenix|langfuse|none`) with bundled
  docker-compose files for Arize Phoenix and LangFuse.
- **Deployment** — single entrypoint (`python -m pishkar.server`) and an
  OS-native daemon installer (`python -m pishkar.daemon install`) that
  writes a systemd user unit (Linux) or LaunchAgent (macOS) around it.
