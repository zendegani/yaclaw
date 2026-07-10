# Pishkar

![Status Alpha](https://img.shields.io/badge/Status-Alpha-FF8C00) [![Backend CI](https://github.com/zendegani/yaclaw/actions/workflows/ci-backend.yml/badge.svg?branch=main)](https://github.com/zendegani/yaclaw/actions/workflows/ci-backend.yml) [![Frontend CI](https://github.com/zendegani/yaclaw/actions/workflows/ci-frontend.yml/badge.svg?branch=main)](https://github.com/zendegani/yaclaw/actions/workflows/ci-frontend.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) ![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white) ![Node.js 20+](https://img.shields.io/badge/Node.js-20%2B-339933?logo=nodedotjs&logoColor=white)

**Pishkar** is a personal AI butler that runs locally and talks to you through a modern Web UI or Telegram. It maintains a persistent memory of your interactions by writing to plain markdown files locally, ensuring your data is always under your control.

---

## 🚀 Features

- **Local-First Memory**: Data and sessions are written to human-readable markdown (`~/.pishkar/`) and local SQLite databases.
- **Semantic Recall (optional)**: Set `PISHKAR_EMBEDDING_MODEL` and Pishkar embeds every message locally in SQLite, letting it find past conversations by meaning (`search_memory` tool, sqlite-vec accelerated).
- **Agentic Tool Use & Safety**: Can execute bash, read/write files, and make HTTP requests—guarded by a built-in **Approval Gate** (Ask Me / Allow Once / Allow All) to ensure you stay in control.
- **MCP Extensibility**: Fully wired with the Model Context Protocol (MCP) to seamlessly connect to external tools and services. 
- **Crash-Resilient**: Powered by an SQLite-backed queue with mid-turn crash detection. If your machine reboots, Pishkar resumes exactly where it left off.
- **Cost & Context Aware**: Includes daily token budget enforcement with threshold alerts sent straight to your chat, auto-concise mode, and SHA-256 loop detection to prevent runaway LLM costs.
- **Background Tasks**: Can wake up on a cron schedule (`HEARTBEAT.md`) to do background work without wasting LLM tokens while idle.
- **Built-in Backups**: A `backup` tool archives a consistent snapshot of the session database plus your workspace to any mounted drive (`PISHKAR_BACKUP_DIR`) — schedule nightly runs via `cron.json`.
- **Full Observability**: OpenTelemetry tracing built-in (via Arize Phoenix or Langfuse) so you can see exactly what the LLM is thinking and doing under the hood.
- **Multi-Interface**: Chat via a beautiful Web UI or on the go via Telegram — including voice notes (Whisper STT + optional Piper TTS).
- **Multi-Provider LLM**: Supports Anthropic, OpenAI, Gemini, OpenRouter, Groq, Moonshot, Qwen, and MiniMax.
- **Lightweight**: Easily runs on a Raspberry Pi 5.

## 🛠️ Requirements

- **Python 3.12+** (Install via `uv python install 3.12` if missing)
- [**uv**](https://docs.astral.sh/uv/) (Fast Python package installer)
- **Node.js 20+** & **npm 11.12+** (Only required if running the Web UI)
- **API Key** for your preferred LLM provider.

## 📦 Setup & Installation

### 1. Backend Server

Clone the repository and set up your environment:

```bash
git clone <this repo>
cd yaclaw
uv sync
cp .env.example .env
```

Open `.env` in your favorite editor and paste your LLM provider API key(s).

Start the server:

```bash
uv run python -m pishkar.server
```

*The server listens on `127.0.0.1:8765`. By default, it auto-detects the model based on your configured API keys. To pin a specific model, set `PISHKAR_MODEL=groq/meta-llama/llama-4-scout-17b-16e-instruct` (or any valid LiteLLM ID).*

### 2. Web UI

In a new terminal tab, navigate to the UI directory and start the dev server:

```bash
cd ui
npm install
npm run dev
```

Open **[http://localhost:5173](http://localhost:5173)** in your browser to start chatting.

### 3. Telegram Integration

Prefer chatting on Telegram? Set up a private bot:

1. Message [@BotFather](https://t.me/BotFather), create a `/newbot`, and copy the token.
2. Message [@userinfobot](https://t.me/userinfobot) and copy your numeric User ID.
3. Add the credentials to your `.env` file:

   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   TELEGRAM_OWNER_ID=your_numeric_user_id
   ```

4. Restart the `pishkar.server`.
*Security Note: Only your specific `TELEGRAM_OWNER_ID` can interact with the bot. All other users are silently ignored. Type `/new` in the chat to start a fresh session.*

### 4. Voice notes (optional)

Pishkar can transcribe Telegram voice notes (STT via Groq Whisper, free tier) and optionally reply with synthesized voice (TTS via local Piper).

**Speech-to-text** — set `GROQ_API_KEY` in `.env`, then enable:

```env
PISHKAR_VOICE_ENABLED=1
PISHKAR_STT_ENGINE=groq
```

That alone gets you "speak in Telegram, get a text reply." If the transcript is empty Pishkar will tell you instead of dispatching a turn.

**Text-to-speech (optional)** — install [Piper](https://github.com/rhasspy/piper) and `ffmpeg`, then download a voice model:

```bash
mkdir -p ~/.pishkar/piper
curl -L -o ~/.pishkar/piper/en_US-lessac-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
curl -L -o ~/.pishkar/piper/en_US-lessac-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

Then in `.env`:

```env
PISHKAR_TTS_ENGINE=piper
PISHKAR_PIPER_VOICE=/home/you/.pishkar/piper/en_US-lessac-medium.onnx
```

When TTS is configured, voice-in turns into voice-out (alongside the text reply). If `PISHKAR_TTS_ENGINE` is unset, Pishkar simply replies in text — a fine fallback.

### 5. Webhooks (optional)

Let external systems (GitHub, Home Assistant, IFTTT — anything that can POST) wake Pishkar up. Create `~/.pishkar/webhooks.json`:

```json
[
  {
    "name": "gh-ci",
    "user_id": "ali",
    "secret": "a-long-random-string",
    "prompt": "CI finished. Summarize the payload for me."
  }
]
```

Then point the sender at `POST http://<host>:8765/webhook/gh-ci` with the secret in the `X-Pishkar-Secret` header. The request body (JSON or text) is handed to the agent as a message. Edits to `webhooks.json` apply immediately — no restart needed.

Webhook messages run **untrusted** by default: the agent can read and reply but gets no tools, since payloads are attacker-controllable text. Raise `"trust_level"` per hook only if you trust the sender. Note the server binds to `127.0.0.1`, so expose it deliberately (reverse proxy, Tailscale, SSH tunnel) if the sender is remote.

---

## 🔭 Observability (optional)

Pishkar always keeps an append-only SQLite audit log. For a visual LLM-trace UI, bring up one of the bundled backends:

```bash
# Arize Phoenix — the default; single container, fits a Pi 5
docker compose -f deploy/docker-compose.phoenix.yml up -d

# LangFuse v2 — richer dashboards; better suited to a VPS
docker compose -f deploy/docker-compose.langfuse.yml up -d
```

Then select the backend in `.env` (`PISHKAR_TRACE_BACKEND=phoenix|langfuse|none`) and install the matching extra (`uv sync --extra phoenix` or `--extra langfuse`). Phoenix works with zero further config; LangFuse needs the project keys copied into `.env` — details in the compose file headers and `.env.example`.

---

## 🍓 Running on a Raspberry Pi 5

Pishkar is designed to be lightweight enough for a Pi 5 running 64-bit Raspberry Pi OS.

```bash
# 1. Install system prerequisites
sudo apt update && sudo apt install -y git curl

# 2. Install uv and Python 3.12
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12

# 3. Setup Pishkar
git clone https://github.com/zendegani/yaclaw.git && cd yaclaw
uv sync
cp .env.example .env && $EDITOR .env

# 4. Run
uv run python -m pishkar.server
```

**Tips for Pi Deployment:**

- **Background Service**: From the repo root, `uv run python -m pishkar.daemon install` writes a systemd user unit (a LaunchAgent on macOS) wrapping the same server entrypoint, then prints the `systemctl --user enable --now` / `loginctl enable-linger` commands to activate it. `… pishkar.daemon uninstall` removes it.
- **Remote Access**: Access the Web UI from your laptop securely using SSH port forwarding:

  ```bash
  ssh -L 5173:localhost:5173 -L 8765:localhost:8765 user@your-pi-address
  ```

---

## 📁 Where Your Data Lives

You own your data. Everything is stored locally on your machine:

- 🗄️ **`~/.pishkar/sessions.db`**: An SQLite log of all messages, dialogue turns, tool calls, and events.
- 📝 **`~/.pishkar/users/<user_id>/`**: Contains markdown files (`SOUL.md`, `USER.md`, `AGENTS.md`, `HEARTBEAT.md`) that act as the agent's living workspace. The agent actively reads and edits these.

*Want to start completely fresh? Just delete the `~/.pishkar/` directory.*

---

## 📜 License

This project is open-source and licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.
