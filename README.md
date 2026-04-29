# Pishkar

A personal AI butler. Runs locally, talks to you over a web UI or Telegram, and writes its memory to plain markdown files in `~/.pishkar/`.

## Requirements

- Python 3.14 (`uv python install 3.14` if you don't have it)
- [`uv`](https://docs.astral.sh/uv/) for Python deps
- Node 20+ and `npm` — only if you want the web UI
- An API key for at least one LLM provider (Anthropic, OpenAI, Gemini, OpenRouter, Groq, Moonshot/Kimi, or Qwen via DashScope)

## Setup

```bash
git clone <this repo>
cd yaclaw
uv sync
cp .env.example .env
$EDITOR .env   # paste at least one provider key
```

That's it for the backend.

## Run

```bash
uv run python -m pishkar.server
```

Server listens on `127.0.0.1:8765`. By default it picks a model based on which key is set; pin one with `PISHKAR_MODEL=groq/meta-llama/llama-4-scout-17b-16e-instruct` (or any LiteLLM model id).

### Web UI

```bash
cd ui && npm install && npm run dev
```

Open http://localhost:5173.

### Telegram

1. Message [@BotFather](https://t.me/BotFather), `/newbot`, copy the token.
2. Message [@userinfobot](https://t.me/userinfobot), copy your numeric ID.
3. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_OWNER_ID=...
   ```
4. Restart the server. Only your own user ID can talk to the bot; everyone else is silently ignored. `/new` in chat starts a fresh session.

## Run on a Raspberry Pi 5

Same steps. Tested with 64-bit Raspberry Pi OS:

```bash
sudo apt update && sudo apt install -y git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.14
git clone <this repo> && cd yaclaw
uv sync
cp .env.example .env && $EDITOR .env
uv run python -m pishkar.server
```

To keep it running after logout, wrap it in a systemd unit pointing at `uv run --directory /path/to/yaclaw python -m pishkar.server`. The Pi only needs to reach the LLM provider's API and Telegram; no inbound ports.

## Where your data lives

- `~/.pishkar/sessions.db` — SQLite log (messages, turns, tool calls, events)
- `~/.pishkar/users/<user_id>/{SOUL,USER,AGENTS,HEARTBEAT}.md` — workspace markdown the agent reads and edits

Both are local. Delete to start over.
