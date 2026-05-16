"""Telegram channel — PTB-based bot bridging a Telegram chat to the Gateway.

Single-user model: only the configured `owner_id` may interact; messages
from any other user are silently ignored. One persistent `session_id`
per chat, rotated by `/new`.

Streamed text deltas are buffered and posted as a single message on
`TurnEnd`. Telegram rate-limits message edits to ~1/sec/chat, so live
edit-streaming buys little for chat-length replies and is deferred.

Tool calls are silent by default. Approval gates render as a message
with two inline-keyboard buttons (allow once / deny); the callback
resolves directly through the injected `ApprovalRouter`.
"""

import contextlib
import html
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from pishkar.core.events import (
    ApprovalRequest,
    ContentBlockDelta,
    Event,
    SessionChanged,
    TextDelta,
    TurnEnd,
    UserMessage,
)
from pishkar.core.messages import InboundMessage
from pishkar.gateway.approval_router import ApprovalRouter
from pishkar.gateway.gateway import Gateway
from pishkar.gateway.user_registry import UserChannelRegistry
from pishkar.runtime import ModelSelector, provider_for_model
from pishkar.tools.approval_gate import ApprovalDecision
from pishkar.voice import Synthesizer, Transcriber
from pishkar.voice import dispatcher as voice_dispatcher
from pishkar.workspace.store import SessionStore

logger = logging.getLogger(__name__)


SendMessage = Callable[..., Awaitable[Any]]


class TelegramChannel:
    """Channel-protocol adapter for one Telegram chat / one session.

    `inbound()` is intentionally empty: PTB pushes updates via handlers,
    so the runner forwards messages to the gateway directly. Outbound
    events arrive via `send_event` and are buffered until `TurnEnd`.
    """

    name = "telegram"

    def __init__(
        self,
        *,
        chat_id: int,
        send_message: SendMessage,
        session_id: str = "",
        send_voice: SendMessage | None = None,
        synthesizer: Synthesizer | None = None,
    ) -> None:
        self._chat_id = chat_id
        self._send_message = send_message
        self._send_voice = send_voice
        self._synthesizer = synthesizer
        self._session_id = session_id
        self._buffer: list[str] = []
        self._closed = False
        # Set by the runner before submitting an inbound voice message;
        # consumed on TurnEnd to mirror the modality back to the user.
        self.voice_reply = False

    @property
    def session_id(self) -> str:
        return self._session_id

    def set_session_id(self, session_id: str) -> None:
        self._session_id = session_id

    async def inbound(self) -> AsyncIterator[InboundMessage]:
        if False:  # pragma: no cover — keeps the signature an async-gen
            yield
        return

    async def send_event(self, event: Event) -> None:
        if self._closed:
            return
        if isinstance(event, UserMessage):
            # Mirror sibling-channel user messages into this chat so the
            # conversation reads coherently across devices. Suppress the
            # echo of our own channel — Telegram already shows it.
            if event.channel and event.channel != self.name:
                source = event.channel
                content = event.content
                await self._safe_send(
                    text=f"💬 <i>{html.escape(source)}</i>: {html.escape(content)}",
                    parse_mode="HTML",
                )
            return
        if isinstance(event, ContentBlockDelta) and isinstance(event.delta, TextDelta):
            self._buffer.append(event.delta.text)
            return
        if isinstance(event, TurnEnd):
            text = "".join(self._buffer).strip()
            self._buffer.clear()
            voice_reply = self.voice_reply
            self.voice_reply = False
            if event.stop_reason == "error":
                await self._safe_send(text="⚠️ Something went wrong on that turn.")
            elif text:
                # Telegram caps message length at 4096 chars.
                for chunk in _chunk_text(text, 4000):
                    await self._safe_send(text=chunk)
                if (
                    voice_reply
                    and self._synthesizer is not None
                    and self._send_voice is not None
                ):
                    await self._send_voice_reply(text)
            return
        if isinstance(event, ApprovalRequest):
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "Allow once", callback_data=f"allow_once:{event.request_id}"
                ),
                InlineKeyboardButton(
                    "Deny", callback_data=f"deny:{event.request_id}"
                ),
            ]])
            await self._safe_send(
                text=_approval_message(event.tool_name, event.input),
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            return
        if isinstance(event, SessionChanged):
            # Only react when the new session is somewhere *other* than the
            # one this chat is currently attached to. Keeps the originator
            # quiet — they already know they just /new'd.
            if event.session_id == self._session_id:
                return
            short = event.session_id[:8]
            await self._safe_send(
                text=(
                    f"🔔 {event.source_channel} started a new session "
                    f"(<code>{html.escape(short)}</code>). "
                    "Send /switch to follow, or keep replying here."
                ),
                parse_mode="HTML",
            )

    async def close(self) -> None:
        self._closed = True

    async def _safe_send(self, **kwargs: Any) -> None:
        try:
            await self._send_message(chat_id=self._chat_id, **kwargs)
        except Exception:
            logger.exception("telegram send failed for chat %s", self._chat_id)

    async def _send_voice_reply(self, text: str) -> None:
        """Synthesize `text` and post it as a Telegram voice note. Errors
        here must not block the text reply that already went out."""
        assert self._synthesizer is not None and self._send_voice is not None
        try:
            audio = await self._synthesizer.synthesize(text)
        except Exception:
            logger.exception("voice synthesis failed for chat %s", self._chat_id)
            return
        try:
            await self._send_voice(chat_id=self._chat_id, voice=audio)
        except Exception:
            logger.exception("telegram send_voice failed for chat %s", self._chat_id)


def _chunk_text(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [text]


def _format_usage_section(
    label: str, rows: list[tuple[str, int, int]]
) -> str:
    head = f"<b>{html.escape(label)}</b>"
    if not rows:
        return f"{head}\n<i>nothing yet</i>"
    lines = [head]
    total_in = total_out = 0
    for model, in_tok, out_tok in rows:
        total_in += in_tok
        total_out += out_tok
        lines.append(
            f"• <code>{html.escape(model)}</code> — "
            f"{in_tok:,} in / {out_tok:,} out"
        )
    lines.append(f"<b>Total:</b> {total_in:,} in / {total_out:,} out")
    return "\n".join(lines)


def _approval_message(tool_name: str, args: dict[str, Any]) -> str:
    """Render an approval prompt with a tool-specific headline so the
    user sees *what* is being asked, not just the tool name."""
    head = f"<b>{html.escape(tool_name)}</b> wants to run."
    detail = _approval_detail(tool_name, args)
    if detail:
        return f"{head}\n{detail}"
    return head


def _approval_detail(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "bash":
        cmd = args.get("cmd")
        if isinstance(cmd, str):
            return f"<pre>{html.escape(cmd[:1000])}</pre>"
    elif tool_name == "write_file":
        path = args.get("path")
        content = args.get("content", "")
        if isinstance(path, str):
            length = len(content) if isinstance(content, str) else 0
            return f"<code>{html.escape(path)}</code> ({length} chars)"
    elif tool_name == "http":
        url = args.get("url")
        method = args.get("method", "GET")
        if isinstance(url, str):
            method_str = method.upper() if isinstance(method, str) else "GET"
            return f"<code>{html.escape(method_str)} {html.escape(url)}</code>"
    elif tool_name == "search":
        query = args.get("query", "")
        engine = args.get("engine", "auto")
        if isinstance(query, str):
            engine_str = engine if isinstance(engine, str) else "auto"
            return (
                f"<code>{html.escape(engine_str)}</code>: "
                f"{html.escape(query[:500])}"
            )
    elif tool_name == "read_url":
        url = args.get("url")
        if isinstance(url, str):
            return f"<code>{html.escape(url)}</code>"
    return ""


class TelegramBotRunner:
    """Owns the PTB `Application` and the per-chat session state.

    `start()` / `stop()` plug into the FastAPI lifespan. Each owner
    chat gets one `TelegramChannel` per active session; `/new` rotates
    the session and re-registers a fresh channel with the gateway.
    """

    def __init__(
        self,
        *,
        token: str,
        owner_id: int,
        user_id: str,
        gateway: Gateway,
        approval_router: ApprovalRouter | None = None,
        store: SessionStore | None = None,
        user_registry: UserChannelRegistry | None = None,
        model_selector: ModelSelector | None = None,
        transcriber: Transcriber | None = None,
        synthesizer: Synthesizer | None = None,
    ) -> None:
        self._token = token
        self._owner_id = owner_id
        self._user_id = user_id
        self._gateway = gateway
        self._approval_router = approval_router
        self._store = store
        self._user_registry = user_registry
        self._model_selector = model_selector
        self._transcriber = transcriber
        self._synthesizer = synthesizer
        self._app: Application[Any, Any, Any, Any, Any, Any] | None = None
        self._sessions: dict[int, str] = {}
        self._channels: dict[int, TelegramChannel] = {}
        self._chat_provider: dict[int, str] = {}

    async def start(self) -> None:
        app = Application.builder().token(self._token).build()
        app.add_handler(CommandHandler("start", self._on_start))
        app.add_handler(CommandHandler("help", self._on_help))
        app.add_handler(CommandHandler("new", self._on_new))
        app.add_handler(CommandHandler("sessions", self._on_sessions))
        app.add_handler(CommandHandler("switch", self._on_switch))
        app.add_handler(CommandHandler("provider", self._on_provider))
        app.add_handler(CommandHandler("model", self._on_model))
        app.add_handler(CommandHandler("usage", self._on_usage))
        app.add_handler(CallbackQueryHandler(self._on_callback))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
        if self._transcriber is not None:
            app.add_handler(MessageHandler(filters.VOICE, self._on_voice))
        await app.initialize()
        await app.start()
        if app.updater is None:
            raise RuntimeError("PTB application has no updater (webhook mode?)")
        await app.updater.start_polling(drop_pending_updates=True)
        self._app = app
        logger.info("telegram bot started, owner_id=%s", self._owner_id)

    async def stop(self) -> None:
        if self._app is None:
            return
        with contextlib.suppress(Exception):
            if self._app.updater is not None:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        for chat_id, channel in list(self._channels.items()):
            session_id = self._sessions.get(chat_id)
            if session_id is not None:
                self._gateway.detach_channel(session_id, channel)
                if self._approval_router is not None:
                    self._approval_router.unbind(session_id, channel="telegram")
                voice_dispatcher.unregister_speaker(session_id)
            if self._user_registry is not None:
                await self._user_registry.unregister(
                    self._user_id, channel.send_event
                )
            await channel.close()
        self._channels.clear()
        self._sessions.clear()
        self._app = None

    # ---- handlers --------------------------------------------------------

    def _is_owner(self, update: Update) -> bool:
        user = update.effective_user
        return user is not None and user.id == self._owner_id

    async def _on_start(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update) or update.message is None:
            return
        await update.message.reply_text(
            "Pishkar ready. Send a message; /help for commands."
        )

    async def _on_help(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update) or update.message is None:
            return
        await update.message.reply_text(
            "/new — start a fresh session\n"
            "/sessions — list recent sessions\n"
            "/switch latest — jump to the most recent session\n"
            "/switch <id-prefix> — switch to a specific session\n"
            "/provider — choose LLM provider (Anthropic / Groq / …)\n"
            "/model — choose model within the selected provider\n"
            "/usage — show token spend (today / 7d / all)\n"
            "/help — this message"
        )

    async def _on_new(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update) or update.message is None:
            return
        chat_id = update.message.chat_id
        new_id = await self._mint_session()
        await self._rotate_session(chat_id, session_id=new_id)
        if self._user_registry is not None:
            await self._user_registry.broadcast(
                self._user_id,
                SessionChanged(
                    session_id=new_id,
                    user_id=self._user_id,
                    source_channel="telegram",
                ),
                exclude_session=new_id,
            )
        await update.message.reply_text("Started a fresh session.")

    async def _on_sessions(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update) or update.message is None:
            return
        if self._store is None:
            await update.message.reply_text("Session store unavailable.")
            return
        rows = await self._store.recent_sessions_for_user(self._user_id, limit=10)
        if not rows:
            await update.message.reply_text("No sessions yet.")
            return
        chat_id = update.message.chat_id
        current = self._sessions.get(chat_id)
        lines = ["Recent sessions:"]
        for r in rows:
            sid = r["session_id"]
            marker = " ← current" if sid == current else ""
            lines.append(
                f"<code>{html.escape(sid[:8])}</code> "
                f"({r['message_count']} msgs, {r['last_channel'] or '—'})"
                f"{marker}"
            )
        lines.append("Use <code>/switch &lt;prefix&gt;</code> or <code>/switch latest</code>.")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _on_switch(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update) or update.message is None:
            return
        if self._store is None:
            await update.message.reply_text("Session store unavailable.")
            return
        args = ctx.args or []
        chat_id = update.message.chat_id
        target = await self._resolve_switch_target(args)
        if target is None:
            await update.message.reply_text(
                "Usage: /switch latest  or  /switch <id-prefix>"
            )
            return
        if target == self._sessions.get(chat_id):
            await update.message.reply_text("Already on that session.")
            return
        await self._rotate_session(chat_id, session_id=target)
        await update.message.reply_text(
            f"Switched to session {target[:8]}."
        )

    async def _resolve_switch_target(self, args: list[str]) -> str | None:
        assert self._store is not None
        if not args:
            return None
        token = args[0].strip().lower()
        if token == "latest":
            return await self._store.latest_session_for_user(self._user_id)
        rows = await self._store.recent_sessions_for_user(self._user_id, limit=50)
        for r in rows:
            sid: str = r["session_id"]
            if sid.startswith(token):
                return sid
        return None

    async def _mint_session(self) -> str:
        if self._store is None:
            return str(uuid4())
        session = await self._store.create_session(self._user_id)
        return session.session_id

    async def _on_message(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update):
            return
        if update.message is None or not update.message.text:
            return
        chat_id = update.message.chat_id
        session_id = await self._ensure_channel(chat_id)
        msg = InboundMessage(
            user_id=self._user_id,
            session_id=session_id,
            channel="telegram",
            content=update.message.text,
        )
        await self._gateway.submit(msg)

    async def _on_voice(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update):
            return
        if (
            self._transcriber is None
            or update.message is None
            or update.message.voice is None
            or self._app is None
        ):
            return
        chat_id = update.message.chat_id
        try:
            tg_file = await self._app.bot.get_file(update.message.voice.file_id)
            audio = bytes(await tg_file.download_as_bytearray())
            text = await self._transcriber.transcribe(
                audio, mime=update.message.voice.mime_type or "audio/ogg"
            )
        except Exception:
            logger.exception("voice transcription failed for chat %s", chat_id)
            await update.message.reply_text(
                "⚠️ Couldn't transcribe that voice note."
            )
            return
        if not text:
            await update.message.reply_text(
                "⚠️ Empty transcript — try again."
            )
            return
        session_id = await self._ensure_channel(chat_id)
        channel = self._channels.get(chat_id)
        if channel is not None:
            channel.voice_reply = self._synthesizer is not None
        msg = InboundMessage(
            user_id=self._user_id,
            session_id=session_id,
            channel="telegram",
            content=text,
        )
        await self._gateway.submit(msg)

    async def _on_provider(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update) or update.message is None:
            return
        if self._model_selector is None:
            await update.message.reply_text("Model switching unavailable.")
            return
        available = self._model_selector.available()
        if not available:
            await update.message.reply_text(
                "No providers available — set an API key in .env first."
            )
            return
        chat_id = update.message.chat_id
        current_model = self._model_selector.current()
        current_provider = (
            self._chat_provider.get(chat_id) or provider_for_model(current_model) or ""
        )
        if ctx.args:
            arg = ctx.args[0].lower()
            if arg in available:
                self._chat_provider[chat_id] = arg
                await update.message.reply_text(
                    f"Provider set to <b>{html.escape(arg)}</b>. "
                    f"Use /model to pick a model.",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    f"Unknown provider '{html.escape(arg)}'. "
                    f"Available: {', '.join(sorted(available))}",
                    parse_mode="HTML",
                )
            return
        rows = [
            [InlineKeyboardButton(
                f"{'• ' if prov == current_provider else ''}{prov}",
                callback_data=f"pick_provider:{prov}",
            )]
            for prov in sorted(available)
        ]
        await update.message.reply_text(
            f"Current model: <code>{html.escape(current_model)}</code>\n"
            f"Pick a provider:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    async def _on_model(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update) or update.message is None:
            return
        if self._model_selector is None:
            await update.message.reply_text("Model switching unavailable.")
            return
        chat_id = update.message.chat_id
        current_model = self._model_selector.current()
        provider = (
            self._chat_provider.get(chat_id) or provider_for_model(current_model)
        )
        if provider is None:
            await update.message.reply_text(
                "Pick a provider first with /provider."
            )
            return
        models = self._model_selector.models_for(provider)
        if not models:
            await update.message.reply_text(
                f"No models registered for provider '{html.escape(provider)}'.",
                parse_mode="HTML",
            )
            return
        if ctx.args:
            target = ctx.args[0]
            if target in models and self._model_selector.set_model(target):
                await self._persist_model(target)
                await update.message.reply_text(
                    f"Model set to <code>{html.escape(target)}</code>.",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    f"Unknown model for {html.escape(provider)}. Try /model.",
                    parse_mode="HTML",
                )
            return
        rows = [
            [InlineKeyboardButton(
                f"{'• ' if m == current_model else ''}{m}",
                callback_data=f"pick_model:{m}",
            )]
            for m in models
        ]
        await update.message.reply_text(
            f"Provider: <b>{html.escape(provider)}</b>\n"
            f"Current model: <code>{html.escape(current_model)}</code>\n"
            f"Pick a model:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    async def _on_callback(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update):
            return
        query = update.callback_query
        if query is None or query.data is None or query.message is None:
            return
        await query.answer()
        prefix, _sep, payload = query.data.partition(":")
        message = cast(Message, query.message)
        chat_id = message.chat_id

        if prefix == "pick_provider":
            if self._model_selector is None:
                return
            if payload in self._model_selector.available():
                self._chat_provider[chat_id] = payload
            with contextlib.suppress(Exception):
                await query.edit_message_reply_markup(reply_markup=None)
                await query.edit_message_text(
                    text=(
                        f"Provider: <b>{html.escape(payload)}</b>. "
                        f"Use /model to pick a model."
                    ),
                    parse_mode="HTML",
                )
            return

        if prefix == "pick_model":
            if self._model_selector is None:
                return
            ok = self._model_selector.set_model(payload)
            if ok:
                await self._persist_model(payload)
            with contextlib.suppress(Exception):
                await query.edit_message_reply_markup(reply_markup=None)
                if ok:
                    await query.edit_message_text(
                        text=f"Model set to <code>{html.escape(payload)}</code>.",
                        parse_mode="HTML",
                    )
                else:
                    await query.edit_message_text(
                        text=f"Could not set model to {html.escape(payload)}.",
                        parse_mode="HTML",
                    )
            return

        try:
            decision = ApprovalDecision(prefix)
            request_id = payload
        except ValueError:
            return
        session_id = self._sessions.get(chat_id)
        if session_id is None or self._approval_router is None:
            return
        self._approval_router.resolve(session_id, request_id, decision)
        with contextlib.suppress(Exception):
            label = "Allowed" if decision != ApprovalDecision.DENY else "Denied"
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                text=f"{message.text}\n\n→ {label}"
            )

    async def _persist_model(self, model: str) -> None:
        if self._store is None:
            return
        with contextlib.suppress(Exception):
            await self._store.set_pref(self._user_id, "model", model)

    async def _on_usage(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update) or update.message is None:
            return
        if self._store is None:
            await update.message.reply_text("Token-spend store unavailable.")
            return
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        since_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since_week = now - timedelta(days=7)
        epoch = datetime.fromtimestamp(0, tz=UTC)

        sections: list[str] = []
        for label, since in [
            ("Today", since_today),
            ("Last 7 days", since_week),
            ("All time", epoch),
        ]:
            rows = await self._store.tokens_spent_by_model_since(
                self._user_id, since.isoformat()
            )
            sections.append(_format_usage_section(label, rows))

        await update.message.reply_text(
            "\n\n".join(sections), parse_mode="HTML"
        )

    # ---- session lifecycle ----------------------------------------------

    async def _ensure_channel(self, chat_id: int) -> str:
        if chat_id in self._sessions:
            return self._sessions[chat_id]
        # Prefer the user's most recent session across any channel so a
        # phone-then-laptop conversation continues seamlessly. Falls back
        # to a fresh uuid when the user has never spoken before.
        session_id: str | None = None
        if self._store is not None:
            session_id = await self._store.latest_session_for_user(self._user_id)
        return await self._open_session(chat_id, session_id=session_id)

    async def _open_session(
        self, chat_id: int, *, session_id: str | None = None
    ) -> str:
        assert self._app is not None
        sid = session_id or str(uuid4())
        channel = TelegramChannel(
            chat_id=chat_id,
            send_message=self._app.bot.send_message,
            session_id=sid,
            send_voice=self._app.bot.send_voice if self._synthesizer else None,
            synthesizer=self._synthesizer,
        )
        self._sessions[chat_id] = sid
        self._channels[chat_id] = channel
        self._gateway.register_channel(sid, channel)
        if self._approval_router is not None:
            self._approval_router.bind(sid, channel.send_event, channel="telegram")
        if self._user_registry is not None:
            await self._user_registry.register(
                self._user_id, sid, channel.send_event
            )
        if self._synthesizer is not None and self._app is not None:
            voice_dispatcher.register_speaker(
                sid, self._make_speaker(channel)
            )
        return sid

    def _make_speaker(
        self, channel: TelegramChannel
    ) -> voice_dispatcher.Speaker:
        """Build the per-channel speaker callback for `voice_dispatcher`.

        Suppresses the implicit voice-mirror so a `speak` tool call after
        a voice-in turn doesn't emit two voice notes.
        """
        async def _speak(text: str) -> None:
            channel.voice_reply = False
            await channel._send_voice_reply(text)

        return _speak

    async def _rotate_session(
        self, chat_id: int, *, session_id: str | None = None
    ) -> None:
        old_channel = self._channels.pop(chat_id, None)
        old_session = self._sessions.pop(chat_id, None)
        if old_channel is not None and old_session is not None:
            self._gateway.detach_channel(old_session, old_channel)
            if self._approval_router is not None:
                self._approval_router.unbind(old_session, channel="telegram")
            if self._user_registry is not None:
                await self._user_registry.unregister(
                    self._user_id, old_channel.send_event
                )
            voice_dispatcher.unregister_speaker(old_session)
            await old_channel.close()
        await self._open_session(chat_id, session_id=session_id)


__all__ = ["TelegramBotRunner", "TelegramChannel"]
