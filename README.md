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

Open <http://localhost:5173>.

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

To access the Web UI from your laptop, you can forward the ports over SSH:
`ssh -L 5173:localhost:5173 -L 8765:localhost:8765 user@server-address`
Then open <http://localhost:5173> on your laptop.

## Where your data lives

- `~/.pishkar/sessions.db` — SQLite log (messages, turns, tool calls, events)
- `~/.pishkar/users/<user_id>/{SOUL,USER,AGENTS,HEARTBEAT}.md` — workspace markdown the agent reads and edits

Both are local. Delete to start over.

---

Quick clarification first: the dialog **already** renders the args as a JSON block under the title (`ApprovalDialog.tsx:29-33`, scrollable, `max-h-64`). So for `bash("ls")` you should be seeing:

```
{
  "cmd": "ls"
}
```

If you're not seeing it at all, that's a bug worth checking (browser zoom, dialog truncation). If you *are* seeing it but find it noisy — agreed, JSON-quoting a shell command is awkward.

My take on what to change:

- **Add a per-tool headline** above the JSON, surfacing the one field that matters:
  - `bash` → render `args.cmd` as a `<pre>` shell block (no JSON quoting).
  - `write_file` → headline `path` + `(N chars)`; the JSON pre block already handles the scroll for long content.
  - `http` → headline `METHOD url`.
- **Keep the full JSON** below as-is for the long-tail / unknown tools. It's the safety net.
- The existing `max-h-64 overflow-auto` already prevents long writes from blowing up the dialog — it just becomes a scrollable preview, which is fine.

The "long text into a file" concern is real but not unique to approval — at some point you trust the path enough to not re-read every byte. Showing `path` + length + scrollable preview is the right tradeoff: glanceable headline, full content available if you want to scroll.

Want me to implement the headline for `bash`/`write_file`/`http`? Small change, ~20 lines in `ApprovalDialog.tsx`.

---

Type-check clean. Changes to `ui/src/components/ApprovalDialog.tsx`:

- **`bash`** — shows `$ <cmd>` in a scrollable shell-style block (no JSON quoting).
- **`write_file`** — shows `path: <path> (<N> chars)` so you see at a glance what's being written and how big.
- **`http`** — shows `METHOD url`.
- **Full arguments** — moved into a collapsed `<details>` so it's still there if you want it (paths can be wrong, content can be junk), but doesn't dominate the dialog. Click "Full arguments" to expand.
- Unknown/future tools fall through to just the `<details>` block (still readable, no regression).

Test plan: refresh the UI, ask Pishkar to `ls`. You should see `$ ls` in a tidy block, plus a "Full arguments" toggle.
