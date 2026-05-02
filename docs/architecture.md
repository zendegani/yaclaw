# Pishkar Architecture Analysis

A comparative study of fifteen open-source agent runtimes ("Claws") and the architectural decisions adopted for **Pishkar** — a personal AI butler. Each section examines one architectural concern, surveys how the reference projects handle it, analyzes the trade-offs, and states Pishkar's adopted approach with the reasoning behind it. Future contributors can trace why each decision was made and what alternatives were considered.

A companion comparison matrix in `claws.md` summarizes every project's position on every feature in a single tabular view. Concrete implementation artifacts (folder structure, hexagonal architecture diagram, day-one scope, safety minima checklist, deferred-but-pre-paid items, v2 patterns, footnotes) live in `design.md`.

## Reference projects

| Project | Language | Stance | Distinguishing feature |
|---|---|---|---|
| **OpenClaw** | TypeScript | Full-feature reference | Local-first Gateway control plane; workspace markdown (`SOUL.md`, `AGENTS.md`, `TOOLS.md`) injected as personality; 20+ channels |
| **Hermes** | Python + TS | Mature, opinionated | Self-improving skills, FTS5 session search, six terminal backends (local/Docker/SSH/Daytona/Modal/Singularity), RL training loop |
| **nanobot** | Python | Small and readable core | One loop, many channels, MCP-ready, "two steps to add a provider" |
| **nanoclaw** | TypeScript | Minimum viable | Channels → SQLite → polling loop → container; one process; code-as-config |
| **PicoClaw** | Go | Ultra-compact | SubTurn (subagents), Hooks (event interceptors), Steering (message injection), rule-based model routing |
| **IronClaw** | Rust | Security-first | WASM tool sandboxing, PostgreSQL + pgvector with Reciprocal Rank Fusion search, prompt-injection detection |
| **TinyClaw** | TypeScript | Multi-agent teams | Spawns Claude/Codex CLI processes per agent workspace; SQLite WAL queue; `@mention` routing |
| **KrillClaw** | Zig | Microcontroller-class | Vtable transports (HTTP/BLE/Serial), hand-rolled JSON, priority-based context truncation |
| **microclaw** | Rust | Unified loop | `sqlite-vec` for semantic memory; background reflector that extracts and dedupes facts; per-chat tool authorization |
| **RT-Claw** | C (ESP32) | Embedded swarm | OSAL across FreeRTOS/RT-Thread/Linux; swarm capability bitmap for distributed tool invocation |
| **NemoClaw** | TypeScript + Shell | NVIDIA sandbox wrapper | Hardened Docker + Landlock + seccomp + netns; YAML blueprints; routed inference abstraction |
| **ZeptoClaw** | Rust | Compact production | ~6 MB binary; SHA256 loop detection; hot-config reload; tiered context compaction (70/90/95%) |
| **ApexClaw** | Go | Telegram-native | 100+ tools including browser automation (headless Chrome); Maton API to skip OAuth complexity |
| **MimiClaw** | C (ESP32-S3) | $5 chip butler | Self-scheduling via `cron.json`; `HEARTBEAT.md` plain-text task list that the agent itself edits |
| **Mercury** | TypeScript | Governance-first butler | "Second Brain" with ten typed memory categories; permission-gated tools with folder scoping; OS-native daemon; token budget enforcement |

---

## 1. Execution pattern

The most foundational decision in any agent runtime is the execution pattern: how much autonomy the LLM has, how predictable outputs are, and where deterministic structure lives. This choice cascades into cost profile, evaluation strategy, error handling, and trust boundaries.

### Patterns across the reference projects

Five execution subcategories are observable in the field:

* **Reactive Loop** — Driven strictly by human turns (Wait → Respond → Sleep). (OpenClaw, nanoclaw, PicoClaw, nanobot, KrillClaw).
* **Goal-Driven Autonomous** — Receives an objective and executes a ReAct loop until complete. (Pishkar, Mercury, IronClaw, ZeptoClaw, ApexClaw, MimiClaw).
* **Plan & Execute** — Agent creates a plan, spawns isolated sub-agents/sub-tasks, then summarizes. (TinyClaw, microclaw, Hermes).
* **Workflow-Routed** — LLM is constrained to a deterministic flowchart or DAG of predefined steps. (NemoClaw).
* **Swarm / Distributed** — Multiple networked agents or physical hardware nodes coordinating peer-to-peer. (ZeptoClaw, RT-Claw).

The field has converged: the toy systems are conversational, the production-grade systems are hybrid or autonomous-with-controls. No serious project ships pure workflow as a butler, and no serious project ships pure autonomous without surrounding governance.

### Trade-offs

Hybrid systems require additional architectural seams upfront — Channel, Gateway, hooks — but allow autonomy to be tuned per-request rather than per-rewrite. The same runtime can serve a quick conversational query and a delegated multi-step task without code changes; only the agent's bounded mode shifts.

### Pishkar's adopted approach

Pishkar uses the **hybrid** pattern, in the lineage of OpenClaw, Mercury, and PicoClaw. A deterministic Gateway (channel routing, queueing, hooks) wraps an autonomous agent loop in the middle. The same runtime serves both modes: a quick "what is on my calendar" remains conversational with the human steering each turn, while "research the top five SaaS competitors and draft a memo" is delegated to the autonomous part with the agent loop running until the goal is complete.

This choice anchors most subsequent decisions: the Channel and Gateway abstractions become non-negotiable, the agent loop must be hand-rollable to support custom hooks and steering (§2), and the safety architecture (§5) must wrap the loop deterministically.

---

## 2. Agent loop implementation

The agent loop — the `run_turn(messages, tools) → events` function — is the heart of the runtime. The decision is whether to hand-roll the loop or delegate it to an SDK. The choice is semi-irreversible: moving between hand-rolled and SDK-based loops later is a real refactor, and the choice determines whether custom in-loop primitives (Hooks, Steering, SubTurn) are possible at all.

### Patterns across the reference projects

Three implementation shapes appear across the fifteen:

* **Hand-rolled loops** (12 of 15) — OpenClaw, PicoClaw, KrillClaw, ZeptoClaw, microclaw, IronClaw, MimiClaw, nanobot, ApexClaw, NemoClaw, RT-Claw, Hermes. Each project writes its own ReAct-style loop with custom hooks, compaction, and loop-detection primitives. (Matches `claws.md` but *extends* it by enabling **Mid-run Steering**: PicoClaw, microclaw, Hermes, and OpenClaw support interrupting, injecting messages mid-run, or streaming intermediate outputs).
* **SDK-as-engine** (2 of 15) — Mercury delegates the loop to Vercel AI SDK v4 (`generateText` / `streamText`); nanoclaw delegates the loop to Anthropic's Claude Agent SDK directly. Streaming, retry, and multi-provider concerns come for free; in-loop hooks become awkward to insert.
* **CLI-as-runtime** — TinyClaw (renamed to TinyAGI) shells out to the `claude` or `codex` CLI per turn and parses stdout. Fast prototype, fragile long-term.

The field has predominantly converged on hand-rolled because hooks, custom compaction, and embedded targets all require loop-level access; the two SDK-as-engine outliers represent a deliberate trade-off of in-loop control for development speed.

### Trade-offs

Hand-rolling costs roughly three to five extra days upfront compared to SDK-as-engine. In return, every governance primitive — approval gate, budget enforcement, loop detector, tool-aware compaction — lives where it is naturally expressible. SDK-as-engine ships faster but cannot put custom code between the model response and tool execution, which is precisely where Hooks live.

### Pishkar's adopted approach

Pishkar **hand-rolls** its `run_turn` function. The loop integrates the Hooks / Steering / SubTurn primitives observed in PicoClaw, the SHA256 loop detection observed in ZeptoClaw, and tool-aware context compaction (assistant prose is dropped before tool results when context tightens, a pattern from KrillClaw). The loop is bounded by a max-turn budget (Mercury's value of 10) as an independent stop condition.

The hand-rolled choice is a precondition for the governance layer in §5: the approval gate, token budget, and per-tool timeout all require code paths inside the loop that an SDK would not expose.

---

## 3. Memory & state

Memory determines whether the agent feels stateful — whether it knows the user — or stateless. The decision is also semi-irreversible: schema choices made on day one shape what is possible later, and migrations against an active conversation log are painful.

### Patterns across the reference projects

Five memory subcategories appear:

* **File-based Workspace** — Storing facts purely in plain-text markdown files (e.g., `USER.md`). (Pishkar, OpenClaw, MimiClaw).
* **Append-only Database** — Standard session logs via SQLite/Postgres without advanced search. (Pishkar, nanoclaw, NemoClaw, TinyClaw, PicoClaw, RT-Claw).
* **Semantic Vector Recall** — RAG-style retrieval using `pgvector`, `sqlite-vec`, or embeddings. (IronClaw, microclaw, ApexClaw, Hermes).
* **Typed Knowledge Graph** — Explicit, typed schema categories separating relationships from rules. (Mercury).
* **Active Context Compaction** — Dynamic context management like tiered sliding windows or token budgets. (Mercury, ZeptoClaw, nanobot, KrillClaw).

### Trade-offs

Mercury's typed memory is what differentiates a butler that feels like it knows the user from a chatbot with a long log. It costs roughly two to three days of additional work and adds schema that requires migration if the categories are guessed wrong. Semantic recall is downstream of typed memory: once the schema is right, `sqlite-vec` is a half-day add, but adding it standalone before the typed schema is premature optimization.

Workspace-as-state has an underrated property: when the user says "from now on, call me Ali," the agent writes to `USER.md` and the fact is persisted without any memory infrastructure. The same affordance generalizes to `HEARTBEAT.md` for self-scheduling and `skills/` for capability accumulation.

### Pishkar's adopted approach

Pishkar uses **workspace markdown + SQLite session log** on day one. Markdown files at `~/.pishkar/users/<user_id>/` (`SOUL.md`, `AGENTS.md`, `USER.md`) are injected into the system prompt; SQLite stores the append-only conversation log keyed by `user_id` and `session_id`. The schema is kept clean enough that microclaw-style reflector and Mercury-style typed categories can be layered on later without migration. Semantic recall via `sqlite-vec` is deferred until the typed schema is in place — adding it standalone before the schema is wrong-order optimization.

---

## 4. Channels (UI surfaces)

The Channel abstraction is non-negotiable: every reference project that scaled cleanly defines a single interface ("receive message, send message, report presence") and every integration (CLI, Web UI, Telegram, Slack, etc.) is a new implementation. Platform-specific code never reaches the agent loop. The decision left open is *which* channels to ship on day one.

### Patterns across the reference projects

* **CLI only** — microclaw, ZeptoClaw — terminal-first.
* **Multi-channel via channel registry** — OpenClaw (20+ channels via Gateway, including Web UI, CLI, Telegram, Slack), nanoclaw (CLI + WhatsApp / Telegram / Slack / Discord / Gmail / Teams / iMessage / Matrix / etc., channel registry self-registers at startup), Mercury (Web UI + CLI + OS daemon), PicoClaw (CLI + multi-channel via Hooks fired on channel events). Common shape: channel registry self-registers at startup; agent loop receives canonical `InboundMessage` regardless of source.
* **Telegram-native** — ApexClaw (Telegram-bound), MimiClaw (ESP32 firmware) — mobile-first or device-first deployments.
* **Embedded transports** — KrillClaw (BLE/Serial/HTTP via vtable), RT-Claw (OSAL across FreeRTOS/RT-Thread/Linux) — specialized hardware.
* **Configuration Surfaces** — Several projects expose UI specifically for agent setup: PicoClaw and ZeptoClaw offer a WebUI Launcher / Setup Wizard, while MimiClaw relies on a UART Serial REPL for NVS-flash configuration.

The pattern across multi-channel projects is that the agent loop receives a canonical `InboundMessage` and emits a canonical `OutboundMessage`; channel-specific concerns (Telegram inline buttons, Slack blocks, WhatsApp templates) live in a free-form `metadata` dict so they never leak into the loop.

### Trade-offs

Adding Telegram on day one is appealing — a mobile butler is the dream — but the governance layer (approval gate inline buttons) needs careful design to render through Telegram's message format, solving two problems at once. Telegram-first (skipping the browser) loses rich tool-call rendering: Telegram is text + simple media, so watching the agent think through a multi-step task is awkward.

A subtle observation across the fifteen: a CLI is often best implemented as a thin client to the same WebSocket the Web UI uses (TinyClaw does this). The "Channel abstraction" stays, but the literal `CLIChannel` ends up as a TUI talking to the same server endpoint.

### Pishkar's adopted approach

**Web UI + CLI as a WebSocket client** on day one. `pishkar serve` launches a FastAPI server with a WebSocket endpoint streaming the typed event protocol (`turn_start` → `message_start` → `content_block_*` → `tool_result` → `turn_end`, modeled on Anthropic's streaming API shape). The Web UI and `pishkar chat` are both clients to that single endpoint — one event stream, two presentation surfaces. Telegram lands later as a clean `Channel` implementation without touching the agent loop.

The event schema is generated from pydantic models on the server with TypeScript codegen on the client, preventing drift between the two implementations.

---

## 5. Safety & governance

Operating an LLM-driven agent that can execute shell commands and edit files requires a layered safety architecture. The question is not whether to enforce safety, but how aggressively on day one, and which constructs are cheap to install now versus expensive to retrofit.

### Patterns across the reference projects

Five safety & governance subcategories appear:

* **Operator Trust** — Minimal safety, relying entirely on the operator. (ApexClaw, nanobot, MimiClaw, RT-Claw).
* **Human-in-the-loop Gates** — Strict approval required for state-mutating tools. (Pishkar, OpenClaw, TinyClaw, Mercury, microclaw, PicoClaw).
* **Container Isolation** — Isolating runs using Docker or MicroVMs per session. (OpenClaw, nanoclaw, Hermes).
* **Process/Kernel Sandboxing** — OS-level restrictions like Landlock, Seccomp, or Firejail. (NemoClaw, ZeptoClaw).
* **Capability-Limited Runtimes** — WASM capability restrictions or compile-time stripping. (IronClaw, KrillClaw).

### Trade-offs

Approval gates introduce friction the first time each tool is invoked. Mercury's triple-answer pattern front-loads that friction so it dissipates after a few confirmations; the alternative ("I will be careful") does not survive contact with the first agent decision to `rm -rf` something. Token budgets share the same shape: easy to defer, painful to discover. A `BudgetedProvider` wrapper around the model interface costs roughly one day to install, runs auto-concise mode above 70% utilization, and prevents a runaway loop or verbose model from quietly burning the daily budget.

Sandboxing tiers (Docker per session, WASM, Landlock+seccomp+netns) are structurally stronger but operationally heavy and poorly matched to a single-operator threat model. They become essential when running untrusted code or when multiple users share the runtime.

### Pishkar's adopted approach

Pishkar ships with **Mercury-equivalent governance** from day one:

* Per-tool timeout (30 s default) and max-result-size cap (1 MB default).
* Loop guard: five identical `(tool_name, args_hash)` invocations in the last ten turns triggers a stuck-state error.
* Maximum turn budget of ten per agent invocation.
* Append-only conversation log, replay-capable.
* `secrets.env` (git-ignored) separated from `config.toml` (committable).
* `InboundMessage.trust_level` field reserved per channel.
* **Approval gate** (*Ask Me / Allow Once / Allow All This Session*) on bash and write-capable tools, implemented inside the `ToolRunner` interface.
* **Token budget enforcement** via `BudgetedProvider` wrapping `ModelProvider`; auto-concise at 70 % utilization; tracked per `user_id`.

Folder-scope restrictions, per-tool override configuration, and OS-daemon installation (LaunchAgent / systemd / Task Scheduler) are deferred to v2. The interfaces to add them are reserved.

The combined effect is that Pishkar is safe to leave running and cost-disciplined from the first day, without paying for production-grade isolation that the personal-butler threat model does not require.

---

## 6. Tool extensibility

Tools are what makes the agent useful — without them, the runtime is a chatbot with a fancy loop. The fork is whether to commit to **MCP (Anthropic's Model Context Protocol)** as a plugin standard alongside a native `@tool` decorator on day one.

### Patterns across the reference projects

* **Native Code Plugins** — Hardcoded tools written in the host language. (Used by all projects as a baseline).
* **MCP Integration** — Standardized integration with external Model Context Protocol servers. (Pishkar, nanoclaw, microclaw, PicoClaw, nanobot).
* **Dynamic Sandboxed Modules** — Hot-loaded plugins running in strict WASM sandboxes. (IronClaw).
* **Self-Improving Skills** — The agent autonomously writes, evaluates, and edits its own tool code. (Hermes).

### Trade-offs

MCP gives a growing community ecosystem of community tools (filesystem, GitHub, Slack, Notion, Postgres servers, etc.) for the cost of a one-day investment in a small `mcp_bridge` module that understands stdio and HTTP-stream transports. Adding MCP support later is more expensive — the tool-invocation path must be retrofitted to understand both transports. A subtle but important distinction: MCP servers are trusted extensions that the operator explicitly installed, not sandboxable tools. The `bash` tool stays sandboxable; MCP calls do not.

A custom plugin manifest format (with manifests, per-plugin permissions, hot reload) is overengineering for a personal butler — the analysis treats this as YAGNI until there are more than five tools or more than one contributor.

### Pishkar's adopted approach

Pishkar ships with a **native `@tool` decorator + an `mcp_bridge` module**. The `ToolRegistry` reads pydantic signatures via the decorator to build JSON schemas, and the bridge wraps MCP servers (stdio + HTTP-stream) as tools in the same registry. Day-one native tools: `bash`, `read_file`, `write_file`. Adding any future MCP server (filesystem, GitHub, Slack, etc.) is a configuration edit, not code. Anthropic's broader ecosystem (Claude Code plugins, Cowork plugins, Claude.ai connectors) converges on MCP, so Pishkar speaks their language without a translation layer.

---

## 7. Observability & tracing

Visibility into every prompt, response, tool call, tool result, latency, and token cost is essential for debugging, iteration, and trust. The architecture must capture all of this; the fork is which backend renders it.

### Patterns across the reference projects

The append-only conversation log captures everything by construction (every reference project does some form of local persistence). The visualization backends vary:

* **Local SQLite log only** — nanoclaw, microclaw, ZeptoClaw, ApexClaw, etc.
* **OpenTelemetry tracing** — OpenClaw exports traces alongside its workspace-markdown audit.
* **Prometheus + structured logging + per-tenant tracing** — ZeptoClaw is the most production-shaped of the set, with Prometheus metrics export and per-tenant trace tagging.
* **Hooks layer as the documented seam** — PicoClaw exposes `before_tool` / `after_llm` hooks specifically as the observability seam; configurable `gateway.log_level`.
* **LangFuse self-hosted** — nanobot wires LangFuse directly for LLM-specific tracing.
* **Permission-decision audit + token-budget metrics** — Mercury surfaces governance events as first-class telemetry.

Approximately three of the fifteen projects integrate with a third-party LLM observability platform; most rely on local logs.

### Trade-offs

For a personal butler that holds a `USER.md` of facts and may eventually access calendar, email, and banking, **data sovereignty narrows the realistic options**. SaaS LLM-tracing platforms (LangFuse cloud, Helicone, Phoenix Arize cloud, OpenTelemetry → Honeycomb/Datadog) put a third party between the agent and its telemetry, which is inconsistent with the privacy posture of a system that holds personal context.

Local-only (SQLite + a CLI replay tool) is sufficient for the literal "see everything" requirement. A self-hosted visualization UI — flame graphs, cost dashboards, trace inspection — adds real iteration value without giving up data sovereignty. Two self-hostable options realistically fit:

* **Arize Phoenix self-hosted** — single container, OTel-native, ~300 MB resident, persists to a mounted SQLite volume. Trace- and eval-focused UI; lighter cost dashboards. Fits comfortably on an SBC like a Raspberry Pi 5 8 GB alongside the butler itself.
* **LangFuse self-hosted** — richer prompt-management, analytics, and cost dashboards. The current default (`:latest` / v3) requires ClickHouse, which alone wants ~8 GB RAM and is inappropriate for SBC deployment; pinning to `langfuse/langfuse:2` (Postgres-only, ~1.5–2 GB) keeps the option viable on smaller hosts, and the v3 footprint is fine on a VPS with the headroom.

A detail worth being explicit about: the Hooks layer must be **fail-open**. If the observability backend is slow or down, the agent loop must not stall. Emission is wrapped in `asyncio.create_task` with a swallowed-exception handler. Cheap now, painful later when a sluggish exporter freezes the butler.

### Pishkar's adopted approach

**Local SQLite append-only log + a pluggable self-hosted trace backend in Docker, defaulting to Arize Phoenix.** Per-Hook events are emitted to two destinations: (a) the always-on SQLite log (cannot be lost even if the trace UI is down), and (b) a local trace backend running in Docker on the same machine, exposing prompt/response/tool-call traces, latency histograms, and token-cost views. Phoenix is the day-one default because it fits SBC hosts (Raspberry Pi 5 8 GB) without contention; LangFuse is supported as a configuration-only swap (`[observability] backend = "langfuse"`) for operators on a VPS who want its richer prompt-management and cost dashboards. Either exporter is fail-open. The Hooks seam stays generic so further backends (e.g., direct OpenTelemetry to a future Honeycomb account) can be added later without touching the agent loop.

---

## 8. Model provider strategy

Provider abstraction is non-negotiable: every reference project, including embedded ones, has an interface over LLM providers. Hard-coding a single provider has not been the right call in any case observed. The decision is *how* to obtain multi-provider support — by hand-rolling implementations or by adopting an existing unified SDK.

### Patterns across the reference projects

* **Hand-rolled multi-provider abstraction** (most) — Rust/Go/Python projects each implement a `ModelProvider`-equivalent and add concrete implementations one at a time.
* **SDK-as-engine multi-provider** — Mercury uses Vercel AI SDK v4, which gives multi-provider for free, including Mercury's polish of "remember last successful provider" to avoid bouncing between flaky primaries and healthy secondaries. nanoclaw uses Anthropic's Claude Agent SDK natively as the primary engine and adds further provider extensibility via `/add-codex` (OpenAI Codex), `/add-opencode` (OpenRouter / Google / DeepSeek), and `/add-ollama-provider` (local open-weight) skills — each skill copies a provider module into the fork on demand. Provider is configurable per agent group.
* **CLI-as-runtime multi-provider** — TinyClaw delegates entirely by spawning Claude or Codex CLI subprocesses.
* **Rule-based per-task routing** — PicoClaw routes simple tasks to cheap models and complex tasks to premium ones via configurable routing rules.

None of the fifteen use a Python LLM-gateway library directly (LiteLLM is widely deployed elsewhere but not surfaced in the reference set).

### Trade-offs

The "1 vs. 2 vs. 3 implementations" framing misses a layer. The realistic options are:

* **Library-based unified SDK** (LiteLLM, any-llm) — a Python library that translates OpenAI-format calls to 100+ providers; nothing else runs. Pure import.
* **Self-hosted proxy** (LiteLLM proxy mode, Portkey self-hosted, Bifrost, Kong AI Gateway) — a separate service in front of all model calls; adds shared caching, fallbacks, retries, cross-client observability. Worth running once multiple agents share the same model fleet.
* **SaaS gateway** (OpenRouter, Portkey cloud, Cloudflare AI Gateway, TrueFoundry) — someone else runs the gateway; prompts pass through their servers.

Applying the same data-sovereignty filter that motivated LangFuse self-hosted, the SaaS gateways drop out: prompts containing personal context should not flow through third-party servers. What remains is the library and self-hosted-proxy layer.

### Pishkar's adopted approach

**LiteLLM as a Python library + LiteLLM `Router` for failover.** A single `LiteLLMProvider` wraps `litellm.acompletion(...)` behind the `ModelProvider` interface. Day-one configuration: Anthropic primary; OpenAI fallback via `litellm.Router(fallbacks=[...])` with the Mercury-style "remember last successful" flag. Adding Ollama, Cohere, Bedrock, Mistral, or local models is a configuration string change. LiteLLM's built-in trace callbacks (`arize_phoenix`, `langfuse`, and others) compose with the observability layer chosen in §7 — token cost and provider attribution flow into whichever backend is configured without extra wiring.

The proxy mode (Docker) remains an upgrade path if shared caching or cross-client observability becomes worthwhile. The seam is configuration-only: the same `LiteLLMProvider`, with `base_url` pointed at a local proxy.

---

## 9. Multi-user identity

A personal butler may evolve to serve a household — separate individuals using one runtime without their data mixing. The architectural question is whether to reserve the seams now (cheap) or retrofit later (expensive).

### Patterns across the reference projects

* **Single-user implicit** (IronClaw, microclaw, ZeptoClaw, KrillClaw, MimiClaw, nanobot) — assume one operator; no `user_id` reserved.
* **Three-level flexible isolation** (nanoclaw) — per-channel agent (full privacy), shared agent across channels (unified memory, separate conversations), or shared session across channels (one conversation across many surfaces); configurable per-channel via `/manage-channels`. Most sophisticated multi-user model in the set.
* **Per-chat scoping** (lightweight multi-user) — ApexClaw (Telegram `chat_id` keyed), microclaw (per-chat tool authorization), TinyClaw (per-agent workspaces).
* **Multi-tenant infrastructure** — ZeptoClaw is the only one with documented per-tenant tracing — closest in the set to true multi-tenancy.
* **Single-user daemon with deep governance** — Mercury's folder scopes are per-user-shaped but the runtime is single-operator-assumed.

None of the reference projects have a household-style "shared and private facts" UX. That remains a green-field design problem.

### Trade-offs

Two layers should not be conflated. **Session isolation** — each conversation has a `session_id`; history is per-session — is already in the architecture (the Gateway routes by session). **User identity** — separate `USER.md`, separate token budget, separate approval-gate decisions, no leakage between humans — requires explicit design. The cheap reservations during day-one work are: (a) `InboundMessage.user_id` field, (b) SQLite tables include a `user_id` column, (c) workspace path is `~/.pishkar/users/<user_id>/`, (d) channel adapters do `channel_identity → user_id` mapping, (e) token budget and approval-gate decisions are scoped per `user_id`.

The expensive retrofits skipped if the seams are not reserved: SQLite migrations, restructuring the workspace from `~/.pishkar/USER.md` to `~/.pishkar/users/ali/USER.md`, threading `user_id` through every existing code path.

The hard parts of multi-user — per-channel authentication (CLI process UID, Web login, Telegram chat_id allowlist) and the shared-versus-private-facts distinction — are real product decisions. Building them before there is a real second user is premature.

### Pishkar's adopted approach

**Reserve seams; ship single-user UX day one.** Pishkar adds `InboundMessage.user_id` (always `"ali"` by default), SQLite `user_id` column, per-user workspace path, and per-user token-budget / approval-gate scoping during day-one work. Channel adapters do trivial id mapping (hardcoded today; real later). Per-channel authentication and shared-versus-private-facts wait until family is a concrete second user with concrete needs.

The principle is identical to §5's `trust_level` reservation: a single field reservation now pays the cost of a future migration.

---

## 10. Proactive triggers

A butler that only acts when messaged is a chatbot. The fork is how the runtime is woken — by a human message, a clock, or some external sensory event.

### Patterns across the reference projects

* **Reactive only** (most) — IronClaw, microclaw, ZeptoClaw, PicoClaw, ApexClaw, nanobot, KrillClaw — wake on channel messages only.
* **Timer triggers** — MimiClaw (the canonical pattern: `HEARTBEAT.md` plus `cron.json`, with the agent self-editing its task list); Hermes (structured `scheduled_tasks/` directory); nanoclaw (scheduled jobs alongside multi-channel reactive); OpenClaw also implements a `HEARTBEAT.md` + `cron.json` shape ("patrols" and exact-time tasks).
* **Workflow-triggered** — NemoClaw's YAML blueprints route inference based on input shape.
* **Sensor / capability triggers** — RT-Claw's embedded swarm responds to peer events via a capability bitmap; closest in the set to Home-Assistant-style sensory triggers.
* **Multi-channel reactive** — OpenClaw, Mercury, TinyClaw — wake on any channel but no proactive triggers.

None of the fifteen has a documented unified `TriggerSource` abstraction; each project's trigger pattern is project-specific.

### Trade-offs

The narrow framing of "self-scheduling" misses what makes a butler proactive. A butler benefits from many wake sources — timer, channel messages, webhooks, Home Assistant / MQTT, email, calendar, file changes. The right abstraction is a `TriggerSource` interface analogous to `Channel`, where each implementation produces synthetic `InboundMessage` events onto the same queue the Gateway routes.

The cost concern of running the agent loop every minute is addressed by design: **the wake tick is cheap Python; the LLM only runs when there is actually something to do**. Every minute, `HeartbeatTrigger.tick()` reads `HEARTBEAT.md` and `cron.json` (file I/O), checks task timestamps against the current time, and emits a synthetic message only when a task is actually due. Net cost: zero LLM calls when nothing is pending; one LLM call per real due event. The expensive failure mode (poll-LLM-every-minute) is prevented structurally.

### Pishkar's adopted approach

**`TriggerSource(Protocol)` abstraction + `HeartbeatTrigger` on day one.** The interface mirrors `Channel`. The implementation:

```python
class TriggerSource(Protocol):
    async def run(self, emit: Callable[[InboundMessage], Awaitable[None]]) -> None: ...
```

Day-one implementation is `HeartbeatTrigger`: `asyncio.sleep(60)` loop, parses `HEARTBEAT.md` + `cron.json`, emits only when something is due. Future trigger sources (`WebhookTrigger`, `HomeAssistantTrigger`, `MQTTTrigger`, `EmailTrigger`, `FileWatchTrigger`) are each one new class implementing the same Protocol; the agent loop never distinguishes a human message from a trigger event.

---

## 11. Tool sandboxing

Layered with the governance decision in §5, sandboxing addresses a different threat: structural isolation of tool execution rather than permission gating.

### Patterns across the reference projects

* **Subprocess only** (most) — nanoclaw, microclaw, ZeptoClaw, PicoClaw, ApexClaw, nanobot, KrillClaw, Mercury, MimiClaw, TinyClaw. Trust the operator and the tools.
* **Docker per session** — OpenClaw (default for non-main sessions); nanoclaw (Docker per-agent default, with optional Docker Sandboxes micro-VM and Apple Container macOS-native opt-in).
* **Production-grade Linux** — NemoClaw (Landlock + seccomp + netns), Hermes (six isolation tiers).
* **WASM** — IronClaw (capability-based; narrows the tool ecosystem to WASM-compatible tools).
* **OSAL** — RT-Claw uses a per-node operating-system abstraction layer in its embedded swarm.

### Trade-offs

For a personal butler, the threat model is: single user, runs on the operator's own machine, vetted tools. The relevant risks (loop, infinite call, destructive command, runaway memory, runaway runtime) are already addressed by the §5 governance layer (loop guard, max-turn budget, approval gate, max-result-size cap, per-tool timeout). Adding Docker-per-session on day one mostly costs ~100 MB per session in RAM plus operational complexity; new safety against this threat model is marginal.

Real sandboxing earns its keep when running untrusted code, when running someone else's tools, or when provable blast-radius containment is required for compliance reasons. None applies on day one.

A halfway option worth knowing: per-tool resource limits via Python's `resource.setrlimit()` for memory and CPU caps. About half a day of work, no Docker overhead, prevents one tool from eating all RAM. Linux/macOS only.

### Pishkar's adopted approach

**`SubprocessToolRunner` + reserved `ToolRunner(Protocol)` seam.** The day-one runner calls `asyncio.subprocess` directly with the §5 timeout, max-result-size cap, and approval gate. The `ToolRunner` Protocol is reserved so a `DockerToolRunner` (or WASM, or Landlock-based) can land later as a configuration swap when the threat model actually demands it.

---

## 12. Skill system

Skills are how the runtime accumulates capability over time beyond hand-coded tools. A skill is a description of "how to do this kind of task" that the agent can read into context.

### Patterns across the reference projects

* **Workspace markdown skills** — OpenClaw, PicoClaw, MimiClaw, TinyClaw (per agent). Files at `skills/<name>/SKILL.md` are read by the workspace loader and injected into the system prompt.
* **Self-improving skill registry** — Hermes is the only example: hooks fire on task completion (the agent writes a new `SKILL.md`), embedding-search picks relevant skills before future turns, and the agent edits skills when something goes wrong. The distinction between "an agent" and "an agent that gets better."
* **Skills via branches as feature flags** (nanoclaw) — `/add-<channel>` and `/add-<provider>` skills copy modules from long-lived branches (`channels`, `providers`) into the user's fork on demand. A creative inversion of the pattern: skills are not LLM-readable instructions but installable code modules. Distinct from Anthropic's SKILL.md format but shares the design intent that skills live outside trunk.
* **None or hardcoded** — most other projects (Mercury, IronClaw, microclaw, ZeptoClaw, nanobot, RT-Claw, KrillClaw, NemoClaw, ApexClaw with 100+ hardcoded tools).

### Trade-offs

Workspace markdown is the day-one default per the prevailing pattern in the field. Anthropic's existing SKILL.md format (with frontmatter `name`, `description`, optional `model`) is the same one Claude Code, Cowork, and other Anthropic-ecosystem tooling use. Adopting that format means skills are portable across the broader ecosystem.

Once skill count crosses approximately ten, injecting all skills into the system prompt becomes prohibitive in tokens. The next investment is embedding-search-based skill discovery — a small addition (the workspace already has a database, the Hooks already exist) but premature before skill count justifies it.

Self-improvement (Hermes RL pattern) requires three coordinated additions: a skill-discovery mechanism, a skill-write-on-completion hook, and a skill-improve-on-error hook. All three plug into the Hooks system already reserved. This is v2 material; the right time to add it is after five to ten hand-written skills reveal real patterns to systematize.

### Pishkar's adopted approach

**Workspace markdown skills, Anthropic SKILL.md format.** Files at `~/.pishkar/users/<user_id>/skills/<name>/SKILL.md`. The workspace loader reads them at startup, parses frontmatter, and injects matching skills into the agent's system prompt. The agent itself can write new skills via the filesystem tool. Embedding-search discovery is deferred until skill count crosses ~10. Self-improvement wires through the Hooks layer in v2.

---

## 13. Eval & testing

Eval and testing strategies for an agent runtime are largely a green-field problem in the field — most of the reference projects do not document an end-to-end agent evaluation framework.

### Patterns across the reference projects

* **Hermes** uses RL training reward signals as an implicit eval signal through self-improvement.
* **IronClaw** tests WASM sandbox correctness and prompt-injection detection.
* **Mercury** audits permission decisions as a governance correctness signal.
* **ZeptoClaw** tests its loop detector and tiered compaction.
* **microclaw** tests the reflector dedupe logic.
* Most others are not documented.

### Trade-offs

The append-only conversation log mandated in §5 implicitly enables replay-based testing — recorded sessions can be rerun against current code and compared. The fork is what to build day one:

* **Pytest + integration with mocked LLM** is the professional baseline: unit tests for the deterministic components (Gateway, Channels, hooks, ToolRunner, BudgetedProvider, workspace loader), integration tests with a `MockLiteLLMProvider` returning canned responses for predictable runs.
* **Golden-trace replay** loads curated session logs from SQLite and replays them through current code, comparing outputs (deterministic where possible, LLM-as-judge where not). Cannot be done on day one — there are no sessions yet — but the SQLite schema must support it from the start.
* **LLM-as-judge for output quality** uses a stronger or different model to score outputs on a held-out test set. Useful for serious prompt tuning; premature day one.

Skipping testing entirely is reasonable only if iteration is occasional. With a runtime that will be modified weekly, slow regressions go undetected until the butler quietly drifts.

### Pishkar's adopted approach

**Pytest + integration with mocked LLM**, day one. `tests/unit/` covers the deterministic components; `tests/integration/` uses `MockLiteLLMProvider` to verify the loop, approval gate, loop guard, and compaction. CI runs both on every commit. The SQLite session log is structured to support replay tests, so `tests/replay/` lands later without schema migration once real conversations accumulate. LLM-as-judge stays deferred until prompt-tuning becomes concrete.

---

## 14. Backup & sync

Two concerns are often conflated: **backup** (protection against data loss) and **sync** (data accessible from multiple devices). For a personal butler running as a daemon on one machine — accessed from any device via Web UI, CLI, or eventual Telegram — sync is not actually needed: one Pishkar instance, many clients. Sync becomes relevant only if Pishkar runs on multiple machines, which has its own coordination problems (multiple daemons hitting the same SQLite is a corruption hazard).

### Patterns across the reference projects

Backup is not documented in most of the fifteen. Implicit backup-friendliness exists by construction in workspace-markdown projects (OpenClaw, PicoClaw, Hermes, MimiClaw): markdown files are version-controllable; only SQLite complicates a git-only workflow. None of the fifteen has a documented external backup module.

### Trade-offs

The realistic options for a personal butler:

* **Git workspace + scheduled SQLite dumps to a private repo** (private GitHub, self-hosted Gitea, local bare repo on a NAS). Workspace markdown lives in the git repo; SQLite is dumped via `sqlite3 .dump`, committed, and optionally pushed. About half a day to set up, free, version-controlled, with `secrets.env` git-ignored.
* **Whole-folder file-level sync** (Syncthing, Dropbox, iCloud) — works cross-device but the SQLite WAL plus concurrent access from multiple devices is a corruption hazard. Only safe when Pishkar runs on one machine at a time.
* **Built-in backup module** with configurable destinations (S3, Backblaze B2, NAS), scheduled by `cron.json`. About two to three days; most flexible; the right v2 shape if non-technical users will configure backup.

### Pishkar's adopted approach

**Deferred until the question becomes concrete.** The first weeks are dev iteration; nothing accumulates yet that is worth backing up. Interim cover: rely on whatever existing OS-level backup runs on the machine (Time Machine, etc.) and the convention that `~/.pishkar/` lives inside a regular local git repo. The four options above are documented for revisitation around three to four weeks in, when `USER.md` and `sessions.db` start carrying real value.

---

## 15. Runtime deployment

How the runtime actually runs day-to-day — manual command, OS service, Docker stack, or fully-managed daemon. Several earlier choices constrain this: the runtime is a Python `pishkar.server` process, LangFuse runs as a Docker container alongside, and Mercury-style daemon mode is named in the analysis as the eventual endgame.

### Patterns across the reference projects

* **Heavyweight Runtime** — Node.js/Python/Docker environments (>100MB RAM). (Pishkar, OpenClaw, nanoclaw, NemoClaw, TinyClaw, Mercury, nanobot).
* **Compact Binary** — Compiled Rust/Go executables (5–20MB RAM, single binary). (IronClaw, microclaw, ZeptoClaw, PicoClaw, ApexClaw).
* **Embedded Bare-Metal** — C/Zig running directly on Microcontrollers (e.g., ESP32, <2MB RAM). (MimiClaw, RT-Claw, KrillClaw).
* **Serverless Hibernation** — Infrastructure that scales to zero when idle and wakes on demand. (Hermes).

### Trade-offs

Manual invocation is right for active development and iteration; the cost is bad UX for an "always-on butler." OS-native daemon is the pattern Mercury uses and the analysis recommends — clean upgrade path from manual since the entrypoint is a single stable command. Docker compose is appealing because the trace backend (Phoenix or LangFuse) already requires Docker; bundling Pishkar in the same stack is operationally clean but adds Docker as a hard dependency for Pishkar code itself, complicating local debugging cycles.

The seam to reserve from day one is that the entrypoint is one stable command — `python -m pishkar.server` — that works unchanged whether run by a developer in a terminal or by `launchd`/`systemd` later.

### Pishkar's adopted approach

**Manual `python -m pishkar.server` on day one.** Started in a terminal or `tmux` pane during iteration. The entrypoint is a single stable command, so OS-native service installation in week 2 (or whenever manual friction warrants) is just `pishkar install` writing the `LaunchAgent` / `systemd` / Task Scheduler file. Docker compose remains an option for the week the everything-in-Docker shape becomes natural; the same entrypoint script can be invoked inside a container.

---

## 16. Cost dashboards

Cost dashboards are largely already addressed by the decisions in §5 (governance) and §7 (observability). The remaining fork is whether to add anything beyond what is already in scope.

### What is already in scope

* **Self-hosted trace backend** (§7) — per-call, per-trace, per-user cost; cost-by-model and cost-by-tool attribution; trends over time. Phoenix (default) gives per-trace token cost and basic aggregation; LangFuse gives richer daily/weekly/monthly dashboards out of the box. Operators who want the latter swap the backend via config.
* **`BudgetedProvider`** (§5) — daily token budget with auto-concise mode at 70%; tracked per `user_id` (§9).
* **Hooks layer** — the seam for any further cost-aware features.

### Possible additions

* **Cost alerts** — notifications at 50% / 80% / 100% of the daily budget; emitted as synthetic messages into the agent loop ("budget at 80% today, want to switch to a cheaper model?") or as desktop notifications. About half a day.
* **Custom cost panel in the Web UI** — a summary card showing today's spend, monthly trend, top tool/model by cost. About one to two days; pulls from the trace backend's API (Phoenix or LangFuse) or from `BudgetedProvider` state directly.
* **Cost-aware routing** (PicoClaw style) — cheap model for simple tasks, premium for complex. Optimization rather than visualization; deferred per §8 (LiteLLM Router supports it later as a configuration change).

### Pishkar's adopted approach

**Deferred.** The combination of the self-hosted trace backend plus `BudgetedProvider` covers the day-one need. The four extra options above stay live for revisitation when friction is concrete — most likely cost alerts after the first runaway-loop incident, or upgrading to LangFuse if the Phoenix default's cost views start feeling thin.

---

## 17. Resilience

Resilience — the system's ability to recover from crashes, partial failures, and downtime — is threaded through several earlier decisions but warrants its own treatment because some of the most valuable patterns from the reference projects are specifically about graceful recovery.

### Patterns across the reference projects

Resilience tricks observable across the fifteen:

* **Persistent queue + idempotent message flow** (nanoclaw) — `inbound.db` / `outbound.db` separation with exactly one writer per file (no cross-mount contention) plus a 60-second `host-sweep.ts` for stale-session detection, due-message wake, and recurrence. The most thorough message-flow resilience in the set.
* **SQLite-backed queue** (TinyClaw) — per-agent SQLite WAL queue; messages survive restart, agent state independent.
* **Plain-text self-state** (MimiClaw) — `HEARTBEAT.md` is a markdown file the agent edits; survives any reboot by construction. The agent reads its own task list at startup.
* **OS-daemon supervision** (Mercury) — `launchd` / `systemd` restart the process on crash with exponential backoff.
* **Tiered compaction** (ZeptoClaw) — context pressure handled in stages at 70 / 90 / 95 % utilization rather than as a hard cutoff.
* **Hot-config reload** (ZeptoClaw) — config changes do not require restart.
* **Per-session Docker isolation** (NemoClaw, OpenClaw) — a crashed session does not take down the runtime.
* **WASM tool sandboxing** (IronClaw) — a crashing tool cannot take down the agent loop.
* **Multi-backend serving** (Hermes) — six isolation tiers mean partial failure does not equal total failure.
* **Capability bitmap swarm** (RT-Claw) — distributed nodes can take over for a failed peer.
* **Loop guard** (ZeptoClaw, KrillClaw) — SHA256 detection of repeated tool calls breaks runaway agents.

### Trade-offs

Resilience has a high upside-to-cost ratio when added during initial design and a high cost when retrofitted. Most of the cheap additions are pre-paid by the append-only log and SQLite session store decisions: idempotent tool calls, mid-turn detection, and trigger catch-up all become straightforward pattern queries against the existing schema.

Heavier resilience patterns (per-session Docker isolation, multi-backend serving, swarm failover, OS-daemon supervision) bring meaningful operational complexity and are appropriate for production-grade or multi-user contexts. For a single-user personal butler, they are deferred without losing the ability to add them later — none requires rework if added when the threat model justifies it.

### Pishkar's adopted approach

Pishkar inherits a substantial resilience baseline from earlier decisions: append-only conversation log (§5), atomic workspace writes (`design.md` footnotes), loop guard and max-turn budget (§5), fail-open Hooks (§7), tool-aware compaction (§2), and LiteLLM Router failover (§8).

On top of this baseline, day-one work adds five specific resilience constructs:

* **SQLite-backed queue.** The `messages` table doubles as the inter-component queue with a `delivered_at` column; messages survive restart. The Gateway picks up from the last undelivered row on startup, replacing the in-memory `asyncio.Queue` of the original day-one shape.
* **Transactional tool-result commit.** Each tool call carries a UUID; the `tool_call` row and matching `tool_result` row are committed in a single transaction. On startup, the runtime scans for orphaned `tool_call` rows (no matching result) and tags them as `interrupted`. The agent loop sees the interruption and surfaces it to the user: "Previous turn was interrupted at tool call X; retry, abandon, or notify only?"
* **Mid-turn crash detection on startup.** The runtime looks for the last `turn_start` event without a matching `turn_end`. If found, a synthetic system message is injected into the next interaction noting the interruption point, so the user is never left wondering whether the request was processed.
* **Trigger catch-up.** `HeartbeatTrigger.run()` performs a startup pass over `HEARTBEAT.md` and `cron.json`, detecting tasks whose due time was during downtime, and emits each missed task as a synthetic `InboundMessage` with a `was_due_at` timestamp. The agent decides per task whether to run-now or skip-as-stale.
* **Channel reconnection with event replay.** WebSocket clients pass a `last_event_id` cookie when reconnecting; the server replays missed events from the SQLite log. Disconnects (network blip, laptop sleep) do not lose events.

These five additions cost roughly two to three days in aggregate and depend only on schema decisions already made. Corresponding entries are added to the day-one build plan and safety-minima checklist in `design.md`.

The deferred-to-v2 resilience patterns (OS-daemon supervision, per-session Docker isolation, hot-config reload, multi-backend serving, WASM crash isolation) remain on the architecturally pre-paid list.

---

## Summary

The architectures that scale well across the fifteen reference projects converge on a small number of structural decisions: one agent loop, many channels, a tool registry that speaks both native and MCP, a workspace of markdown for state, an event-bus protocol between server and UI, and governance primitives wrapping the loop deterministically. The architectures that do not scale are the ones that let platform-specific code leak into the agent loop or hard-code a single provider.

Pishkar's adopted approach reflects this convergence:

* **Hybrid execution pattern** with a deterministic Gateway wrapping a hand-rolled agent loop.
* **Workspace markdown + SQLite** state, Anthropic SKILL.md format for skills.
* **Web UI + CLI as a WebSocket client** day one; Telegram and other channels as additive `Channel` implementations.
* **Mercury-equivalent governance** (approval gate + token budget + loop guard + max-turn budget) built into the `ToolRunner` and `ModelProvider` seams.
* **Native `@tool` + MCP bridge** for tool extensibility.
* **Local SQLite log + pluggable self-hosted trace backend** (Phoenix default; LangFuse opt-in for VPS) for observability with data sovereignty.
* **LiteLLM library + Router failover** for multi-provider support without per-provider code.
* **`user_id` reservation** for multi-user, with single-user UX day one.
* **`TriggerSource` abstraction + HEARTBEAT.md** for proactive behavior, with cheap-tick design avoiding per-minute LLM cost.
* **Subprocess + `ToolRunner` seam** for sandboxing, with Docker / WASM / Landlock as configuration-only upgrades.
* **Pytest + integration with mocked LLM** for testing, with replay infrastructure reserved.
* **Backup deferred**, **manual `python -m pishkar.server` deployment**, **cost dashboards covered by LangFuse + BudgetedProvider**.
* **Resilience layer** built into the schema from day one — SQLite-backed queue, transactional tool-result commit, mid-turn crash detection, trigger catch-up on startup, and channel reconnection with event replay. The runtime survives crashes and downtime without losing in-flight work or missed triggers.

The important decisions on day one are the seams — `Channel`, `ToolRunner` (with approval gate), `ModelProvider` (with budget wrapper), `TriggerSource`, the event schema, the max-turn-and-loop-detector pair — not the count of features. Most architectural variance between the reference projects that scaled well and the ones that had to be rewritten reduces to whether these seams were laid down correctly on day one.
