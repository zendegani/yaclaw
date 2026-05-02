# Pishkar Implementation Reference

Concrete artifacts derived from the architectural decisions documented in `architecture.md`. This document is the implementation companion: the hexagonal diagram, the Python module layout, the day-one build plan, the deferred features whose seams are pre-paid, the safety minima checklist, the v2 patterns marked for revisitation, and the footnotes / caveats that are inexpensive to address now and expensive to retrofit.

## Hexagonal architecture

The runtime is organized around five core concerns surrounded by pluggable adapters. Channels and Triggers feed `InboundMessage` events into the Gateway; the Gateway routes by session into the Agent Loop; the Agent Loop calls the Model Provider, the Tool Registry, and the Workspace; tool execution flows through the Tool Runner; observability events fan out to SQLite and a pluggable LLM-trace backend (Arize Phoenix by default; LangFuse as an opt-in alternative on hosts with the RAM headroom) via the Hooks layer.

```
       ┌──────────────────────────────────────────────────┐
       │  CHANNELS                                        │
       │    CLIChannel · WebSocketChannel    (day 1)      │
       │    Telegram · Slack · WhatsApp      (later)      │
       └─────────────────────┬────────────────────────────┘
                             │  InboundMessage
                             │  (user_id, trust_level, metadata)
       ┌─────────────────────┴────────────────────────────┐
       │  TRIGGERS                                        │
       │    HeartbeatTrigger                 (day 1)      │
       │    Webhook · HomeAssistant · MQTT   (later)      │
       │    Email · Calendar · FileWatch     (later)      │
       └─────────────────────┬────────────────────────────┘
                             │  synthetic InboundMessage
                             ▼
       ┌──────────────────────────────────────────────────┐
       │  GATEWAY                                         │
       │    session router · SQLite-backed queue · hooks  │
       │    (resumes from last undelivered on restart)    │
       └─────────────────────┬────────────────────────────┘
                             │
                             ▼
       ┌──────────────────────────────────────────────────┐
       │  AGENT LOOP  (run_turn → events)                 │
       │    SHA256 loop detector · tool-aware compaction  │
       │    max-turn budget (10) · streaming events       │
       └──┬─────────────────┬─────────────────┬───────────┘
          │                 │                 │
          ▼                 ▼                 ▼
   ┌────────────┐   ┌────────────────┐  ┌──────────────────┐
   │ Budgeted   │   │  ToolRegistry  │  │   Workspace      │
   │  Provider  │   │   @tool        │  │   ~/.pishkar/    │
   │  ┌──────┐  │   │   + mcp_bridge │  │    users/<id>/   │
   │  │LiteLLM│ │   └────────┬───────┘  │      SOUL.md     │
   │  │Router│  │            │          │      AGENTS.md   │
   │  │      │  │            ▼          │      USER.md     │
   │  └──────┘  │   ┌────────────────┐  │      HEARTBEAT.md│
   │  + budget  │   │   ToolRunner   │  │      cron.json   │
   │  + concise │   │   + Approval   │  │      skills/     │
   │     @70%   │   │     Gate       │  │      sessions.db │
   └─────┬──────┘   │   (subprocess; │  └──────────────────┘
         │          │   Docker later)│
         │          └────────────────┘
         ▼
   ┌────────────┐                       ┌──────────────────┐
   │   Hooks    │                       │    Tests         │
   │ (fail-open)│                       │   unit / integ   │
   │            │   ┌──────────────┐    │   MockLiteLLM    │
   │  ► SQLite  │   │  Phoenix     │    │   replay (later) │
   │    audit   │──►│  (Docker)    │    └──────────────────┘
   │  ► trace   │   │  — default;  │
   │    sink    │   │  LangFuse    │
   │            │   │  swap-in for │
   │            │   │  VPS hosts   │
   └────────────┘   └──────────────┘
```

Everything inside the rounded boxes is core code that is written once and rarely changed. The labels ending in *(day 1)* are present at first launch; *(later)* items are additive — each is one new class implementing an existing Protocol, with no rearchitecting.

## Python module layout

```
pishkar/
├── core/
│   ├── events.py             # Pydantic models for streaming events
│   ├── messages.py           # InboundMessage, OutboundMessage, Turn, Session
│   │                         #   (incl. user_id, trust_level, metadata fields)
│   ├── agent.py              # run_turn loop
│   ├── loop_guard.py         # SHA256 loop detection (5 hashes / 10 turns)
│   └── compaction.py         # tool-aware context trimming
├── channels/
│   ├── base.py               # class Channel(Protocol)
│   ├── cli.py                # thin TUI client over WebSocket
│   ├── ws.py                 # WebSocket channel (used by Web UI + cli.py)
│   └── telegram.py           # later
├── triggers/
│   ├── base.py               # class TriggerSource(Protocol)
│   ├── heartbeat.py          # cheap-tick: parses HEARTBEAT.md + cron.json
│   ├── webhook.py            # later (FastAPI route → synthetic InboundMessage)
│   └── home_assistant.py     # later (HA WebSocket → synthetic InboundMessage)
├── gateway/
│   ├── gateway.py            # queue + session routing + per-user scoping
│   └── hooks.py              # before_tool, after_llm, on_turn_complete, …
├── providers/
│   ├── base.py               # class ModelProvider(Protocol)
│   ├── litellm_provider.py   # wraps litellm.acompletion + Router
│   └── budgeted.py           # BudgetedProvider wrapping ModelProvider
├── tools/
│   ├── registry.py           # @tool decorator (pydantic → JSON schema)
│   ├── runner.py             # ToolRunner(Protocol); SubprocessToolRunner
│   ├── approval_gate.py      # Ask Me / Allow Once / Allow All This Session
│   ├── bash.py
│   ├── fs.py
│   ├── http.py
│   └── mcp_bridge.py         # wraps MCP servers as tools (stdio + HTTP-stream)
├── workspace/
│   ├── loader.py             # reads SOUL.md, AGENTS.md, USER.md, skills/*
│   ├── store.py              # SQLite (aiosqlite) — sessions + history + facts
│   └── atomic_io.py          # temp-file + os.replace for safe writes
├── observability/
│   ├── exporter.py           # fail-open async fan-out
│   ├── sqlite_sink.py        # always-on append-only audit
│   ├── phoenix_sink.py       # default trace sink (LiteLLM callback)
│   └── langfuse_sink.py      # opt-in alternative for VPS hosts
├── server.py                 # FastAPI + WebSocket; one stable entrypoint
└── cli.py                    # `pishkar serve` / `pishkar chat` / `pishkar backup`
```

```
ui/
├── src/
│   ├── api/
│   │   ├── events.ts         # TS mirror of core/events.py (codegen)
│   │   └── socket.ts         # WebSocket client
│   ├── components/
│   │   ├── Chat.tsx
│   │   ├── ToolCall.tsx      # renders tool_use blocks with live output
│   │   ├── Thinking.tsx
│   │   └── ApprovalDialog.tsx
│   └── state/                # zustand or plain reducer
└── index.html
```

```
tests/
├── unit/                     # gateway, channels, hooks, ToolRunner,
│                             #   BudgetedProvider, workspace loader, triggers
├── integration/              # full loop with MockLiteLLMProvider
└── replay/                   # later — load SQLite session logs, replay,
                              #   compare outputs (deterministic + LLM-judge)
```

```
deploy/
├── docker-compose.phoenix.yml   # default: Arize Phoenix (single container)
├── docker-compose.langfuse.yml  # alternative: LangFuse self-hosted (VPS)
└── README.md
```

The TypeScript event types are generated from the Pydantic models with `pydantic-to-typescript` (or `datamodel-code-generator`). Drift between server and client schemas is a common cause of "works until I add a field" bugs; codegen prevents this from day one.

## Day-one build plan

A sequenced punch list derived from the decisions in `architecture.md`. Roughly two weeks of work on a single developer.

1. **Project scaffolding.** `pyproject.toml`, source layout above, pre-commit hooks (ruff, mypy), `.gitignore` covering `secrets.env`, `sessions.db-shm`, `sessions.db-wal`, trace-backend Docker volumes.
2. **Core types.** `events.py` (streaming events mirroring Anthropic's API shape), `messages.py` with `InboundMessage` / `OutboundMessage` carrying `user_id`, `trust_level`, and a free-form `metadata` dict.
3. **Workspace loader.** Reads `~/.pishkar/users/<user_id>/SOUL.md`, `AGENTS.md`, `USER.md`, `skills/<name>/SKILL.md`. Atomic writes via temp-file + `os.replace`.
4. **SQLite session store.** Single DB, append-only, columns include `user_id` and `session_id`. Tables: `sessions`, `messages`, `tool_calls`, `tool_results`, `governance_decisions` (approval-gate audit), `token_spend`.
5. **`ModelProvider` Protocol + `LiteLLMProvider`.** Wraps `litellm.acompletion`. `litellm.Router` configured with Anthropic primary, OpenAI fallback, "remember last successful." Built-in trace callback wired through `litellm.success_callback` (Phoenix by default; LangFuse if `config.toml` selects it).
6. **`BudgetedProvider`.** Wraps `ModelProvider`. Enforces daily token budget per `user_id`, switches to auto-concise mode at 70% utilization.
7. **`ToolRegistry` + `@tool` decorator.** Reads pydantic signatures, builds JSON schemas. Native tools: `bash`, `read_file`, `write_file`, `http`.
8. **`ToolRunner` Protocol + `SubprocessToolRunner`.** Per-tool timeout (30 s default), max-result-size cap (1 MB default), result truncation with notice.
9. **Approval gate.** Lives inside `ToolRunner`. Three answers: *Ask Me / Allow Once / Allow All This Session*. Day-one CLI prompt; same function called via WebSocket dialog from the Web UI.
10. **`mcp_bridge` module.** Speaks MCP stdio + HTTP-stream transports; registers MCP-server tools into the existing `ToolRegistry`. MCP servers configured in `config.toml`.
11. **Agent loop (`run_turn`).** Streaming events; tool-call execution via `ToolRunner`; SHA256 loop detection (5 identical `(tool, args_hash)` in last 10 turns); tool-aware compaction (drops assistant prose before tool results); max-turn budget of 10.
12. **Hooks layer.** `before_tool`, `after_llm`, `on_turn_complete`, `on_tool_result`. Fail-open: emission via `asyncio.create_task` with swallowed exceptions.
13. **Observability sinks.** `sqlite_sink` (always-on append-only) plus a pluggable trace sink (fail-open exporter): `phoenix_sink` by default, `langfuse_sink` as an opt-in alternative. Selected via `config.toml` (`[observability] backend = "phoenix" | "langfuse" | "none"`); the Hooks seam stays identical so the choice is a one-line swap.
14. **`Channel` Protocol + `CLIChannel` (TUI) + `WebSocketChannel`.** Both connect to the same FastAPI WebSocket endpoint.
15. **`Gateway`.** In-memory `asyncio.Queue` (swap to SQLite-backed queue when adding the second channel). Session router. Hook-registration API (reserved seam; populated as needed).
16. **`TriggerSource` Protocol + `HeartbeatTrigger`.** `asyncio.sleep(60)` loop; reads `HEARTBEAT.md` + `cron.json`; emits synthetic `InboundMessage` only when something is due. No LLM cost when nothing pending.
17. **FastAPI server (`pishkar.server`).** Single entrypoint: `python -m pishkar.server`. WebSocket endpoint streaming the typed event protocol.
18. **Web UI.** Minimal React: `Chat.tsx`, `ToolCall.tsx`, `Thinking.tsx`, `ApprovalDialog.tsx`. WebSocket client; types generated from pydantic models.
19. **Tests.** `tests/unit/` for the deterministic components, `tests/integration/` with `MockLiteLLMProvider`. CI on every commit.
20. **Trace backend (Docker).** `deploy/docker-compose.phoenix.yml` brings up Arize Phoenix as a single container with a mounted SQLite volume for persistence (the day-one default; ~300 MB resident, fits comfortably on a Pi 5 8 GB alongside the butler). `deploy/docker-compose.langfuse.yml` is provided for VPS deployments that want LangFuse's richer prompt-management and cost dashboards — pin to `langfuse/langfuse:2` (Postgres-only, ~1.5–2 GB) to avoid the v3 ClickHouse footprint. Exporter wired through Hooks in both cases.
21. **Starter workspace.** Ship a default `~/.pishkar/users/ali/SOUL.md` with a Pishkar persona description and an empty `USER.md`.
22. **Resilience hardening** (augments items 4, 8, 11, 14, 15, 16). Five constructs that make the runtime crash-tolerant; each is cheap because the schema is already laid out by earlier items.
    a. **SQLite-backed queue** — extends item 15. The `messages` table carries a `delivered_at` column; the Gateway scans for rows where `delivered_at IS NULL` on startup and resumes processing in order. Replaces the in-memory `asyncio.Queue` of the original day-one shape.
    b. **Transactional tool-result commit** — extends items 4 and 8. Each `tool_call` row carries a UUID; `tool_call` and `tool_result` rows are committed in one SQLite transaction. On startup the runtime queries for orphaned `tool_call` rows (no matching `tool_result`) and tags them `interrupted`.
    c. **Mid-turn crash detection on startup** — extends item 11. The agent module scans for the last `turn_start` event without a matching `turn_end`. A synthetic system message ("Previous turn was interrupted at <step>; retry, abandon, or notify only?") is injected into the next user interaction.
    d. **Trigger catch-up** — extends item 16. `HeartbeatTrigger.run()` performs a startup pass over `HEARTBEAT.md` and `cron.json`, emits a synthetic `InboundMessage` carrying `was_due_at` for each task whose due time was during downtime; the agent decides per task whether to act now or skip as stale.
    e. **Channel reconnection with event replay** — extends item 14. The WebSocket client passes a `last_event_id` cookie on reconnect; the server replays missed events from the SQLite log. Disconnects (laptop sleep, network blip) do not lose events.

## Architecturally pre-paid (deferred features that land cleanly)

Each item below is a future addition that requires no rearchitecting because the seam is reserved on day one.

* **Telegram channel** — new `Channel` implementation. The Gateway is unchanged.
* **Slack / Discord / WhatsApp channels** — same shape.
* **Webhook trigger** — new `TriggerSource` registering a FastAPI route on `/webhook/{name}`.
* **Home Assistant / MQTT / email / calendar / file-watch triggers** — same shape, each one new class.
* **Docker-per-session sandboxing** — `DockerToolRunner` implementing `ToolRunner`. Swap by configuration.
* **Semantic memory** — `ALTER TABLE` to add an `embedding BLOB` column, load `sqlite-vec`, write a `search_memory` tool. No architectural change.
* **Mercury-style typed memory categories** — additive on top of the existing schema; the Reflector v2 pattern populates the categories.
* **Skills loader** — already in scope; once the count crosses ~10, add embedding-search-based discovery.
* **Multi-user UX** — per-channel auth + shared/private fact distinction. The `user_id` field is already present.
* **Local models (Ollama, vLLM)** — configuration string in the LiteLLM Router; no code change.
* **Provider failover refinement** — already in scope via `litellm.Router`; tune fallback list.
* **OS-native daemon** — `pishkar install` writes a LaunchAgent / systemd / Task Scheduler file invoking the same `python -m pishkar.server` entrypoint.
* **Self-improving skills (Hermes RL)** — three Hooks (skill-write-on-completion, skill-discovery-before-injection, skill-improve-on-error) plug into the existing Hooks layer.
* **External backup** — `BackupBackend(Protocol)` with S3 / B2 / NAS implementations, scheduled by `cron.json`.
* **Cost alerts** — Hook on `BudgetedProvider` thresholds emits a synthetic `InboundMessage` ("budget at 80% today, want to switch models?").

## Out of scope for day one

* **A custom plugin manifest system.** YAGNI until there are more than five tools or more than one contributor — MCP covers community extensibility, the `@tool` decorator covers in-tree extensibility.
* **A DAG workflow engine.** A single ReAct loop plus subagents covers more than 95% of "workflow" use cases.
* **Docker-per-session sandboxing on day one.** The threat model does not require it; the seam is reserved.
* **A vector database.** `sqlite-vec` is the upgrade when context recall actually feels lacking.
* **Multi-agent routing.** One agent is sufficient until it is not.
* **RL / skill self-improvement on day one.** Hand-written skills cover the high-frequency cases first.
* **A dashboard / settings UI.** The workspace is the settings UI; editing `SOUL.md` in any text editor outperforms building a settings form.
* **OpenTelemetry on day one.** Phoenix (default) already speaks OTel under the hood, and a dedicated OTel sink can be added later as a third option without changing the Hook emission path.

## Safety minima

The constructs below are inexpensive to install during day-one work and very expensive to retrofit. All are covered in §5 of `architecture.md`; restated here as a check-list.

| # | Construct | Implementation surface |
|---|---|---|
| 1 | Every tool call routes through `ToolRunner`; never `subprocess.run` in a tool module directly | `tools/runner.py` |
| 2 | Per-tool timeout (default 30 s) and max-result-size cap (default 1 MB) | `tools/runner.py` |
| 3 | Loop guard: 5 identical `(tool_name, args_hash)` in last 10 turns | `core/loop_guard.py` |
| 4 | Append-only conversation log with full replay detail | `workspace/store.py` |
| 5 | `secrets.env` (git-ignored) split from `config.toml` (committable) | repo root |
| 6 | `InboundMessage.trust_level` field reserved per channel | `core/messages.py` |
| 7 | `InboundMessage.user_id` field reserved per message | `core/messages.py` |
| 8 | Max-turn budget (default 10) as an independent stop condition | `core/agent.py` |
| 9 | Approval gate (*Ask Me / Allow Once / Allow All This Session*) inside `ToolRunner` | `tools/approval_gate.py` |
| 10 | Token budget enforcement via `BudgetedProvider`; auto-concise at 70% | `providers/budgeted.py` |
| 11 | Hook emission is fail-open (`asyncio.create_task` + swallowed exceptions) | `observability/exporter.py` |
| 12 | Workspace writes are atomic (temp-file + `os.replace`) | `workspace/atomic_io.py` |
| 13 | Inter-component queue is SQLite-backed (`messages` table + `delivered_at`); Gateway resumes from undelivered on startup | `gateway/gateway.py`, `workspace/store.py` |
| 14 | Tool calls carry UUIDs; `tool_call` and `tool_result` committed in one transaction; orphaned rows tagged `interrupted` on startup | `tools/runner.py`, `workspace/store.py` |
| 15 | Mid-turn crash detection on startup: last `turn_start` without `turn_end` triggers a synthetic system message in the next interaction | `core/agent.py` |
| 16 | Trigger catch-up on startup: tasks due during downtime emitted as synthetic `InboundMessage` with `was_due_at` | `triggers/heartbeat.py` |
| 17 | Channel reconnection: WebSocket client passes `last_event_id`; server replays missed events from SQLite | `channels/ws.py`, `gateway/gateway.py` |

## v2 patterns to revisit

Two patterns from the reference projects are not adopted on day one but are worth marking for revisitation when the runtime has accumulated weeks of conversation history.

### Reflector (microclaw)

A background job that periodically reads recent turns, asks an LLM to extract durable facts, and writes them — deduplicated — to a memory store. The right time to enable this is once the system starts feeling forgetful (typically after a few weeks of usage). The seam: Pishkar's `on_turn_complete` Hook fires for every completed turn; the Reflector subscribes, batches, and runs an extraction pass on a schedule. The extracted facts can populate either a free-form notes table or — if Mercury-style typed categories are also adopted — the typed memory schema.

### Self-improving skills (Hermes RL)

After a successful task completion, the agent writes a new `SKILL.md` describing how to do this kind of task next time. On future turns, a skill-discovery step (embedding search over skill descriptions) selects relevant skills and injects them. When something goes wrong — user correction, error trace, or low-confidence response — the agent edits the relevant skill to incorporate the lesson. This is the distinction between an agent and an agent that gets better. Three coordinated Hooks are required: (a) skill-write-on-completion, (b) skill-discovery-before-injection, (c) skill-improve-on-error. All three plug into the Hooks layer without touching the core loop.

The right time to add this is after five to ten hand-written skills have revealed real patterns to systematize.

## Footnotes & caveats

A handful of small but architecturally load-bearing details that are inexpensive to address during day-one work and expensive to retrofit.

**`OutboundMessage.metadata: dict`.** Telegram has inline buttons, reactions, and message edits; Slack has blocks; WhatsApp has templates. If `OutboundMessage` is just `{text, attachments}`, those features will leak into the agent loop. Reserve a free-form `metadata` dict on both `InboundMessage` and `OutboundMessage`. Day-one channels ignore it; Telegram-day fills it in. Same trick used by OpenClaw.

**`InboundMessage.trust_level`.** Reserve the field even though the only value today is `"full"`. The day a Telegram group channel is added, filtering tools by trust without changing the agent loop becomes important.

**CLI is probably a WebSocket client, not a separate channel.** In practice: `pishkar serve` starts the FastAPI + WebSocket server, and `pishkar chat` is a thin terminal client that connects to the same WebSocket endpoint and renders to the terminal. The `Channel` abstraction stays, but the literal `CLIChannel` ends up unused — the "CLI" is really a TUI talking to the same WebSocket the Web UI uses. TinyClaw and several other reference projects do essentially this.

**Workspace writes must be atomic.** The agent both reads and writes `SOUL.md`, `USER.md`, `HEARTBEAT.md`. Use temp-file + `os.replace()` for every write. A crash mid-write that corrupts `SOUL.md` is recoverable from git but disruptive in the moment.

**Loop-detection threshold.** "Same hash 3× in a row" is too aggressive — some legitimate tools poll (`tail` a log, watch a file). Safer default: same `(tool_name, args_hash)` 5 times within the last 10 turns. Make it configurable per tool.

**MCP versus bash trust boundaries are different.** MCP servers run as separate processes that the operator explicitly installed — they are trusted extensions, not sandboxable tools. The `ToolRunner` sandboxes the `bash` tool; the `mcp_bridge` forwards to an already-trusted process. Conflating the two — attempting to sandbox MCP calls — either breaks MCP or creates a false sense of security.

**Skill selection before skill injection.** Once skill count crosses ~10, injecting all skills into the system prompt blows context. A skill-discovery step (embedding search over skill descriptions) is required before injection. Not day-one work, but when skills are introduced, make them individually embeddable: short titles plus descriptions in the frontmatter.

**Event-schema drift between Python and TypeScript is a silent failure mode.** Set up Pydantic → TypeScript codegen on day one (via `datamodel-code-generator` or `pydantic2ts`). Without codegen, a `tool_result` field mismatch ships at some point and consumes half a day to track down.

**Trace exporter is fail-open.** Whichever backend is selected (Phoenix, LangFuse, or none), if the exporter blocks the agent loop a slow container freezes the butler. Wrap emission in `asyncio.create_task` with a swallowed-exception handler. Cheap now; painful when the exporter slows down and the loop hangs waiting for it.

**One stable entrypoint command.** `python -m pishkar.server` is the entrypoint, whether run by a developer in a terminal or by `launchd` / `systemd` later. The OS-native daemon installation is a thin wrapper that invokes the same command — no second entrypoint.
