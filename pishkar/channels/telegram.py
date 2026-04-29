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
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
    TextDelta,
    TurnEnd,
)
from pishkar.core.messages import InboundMessage
from pishkar.gateway.approval_router import ApprovalRouter
from pishkar.gateway.gateway import Gateway
from pishkar.tools.approval_gate import ApprovalDecision

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
    ) -> None:
        self._chat_id = chat_id
        self._send_message = send_message
        self._buffer: list[str] = []
        self._closed = False

    async def inbound(self) -> AsyncIterator[InboundMessage]:
        if False:  # pragma: no cover — keeps the signature an async-gen
            yield  # type: ignore[unreachable]
        return

    async def send_event(self, event: Event) -> None:
        if self._closed:
            return
        if isinstance(event, ContentBlockDelta) and isinstance(event.delta, TextDelta):
            self._buffer.append(event.delta.text)
            return
        if isinstance(event, TurnEnd):
            text = "".join(self._buffer).strip()
            self._buffer.clear()
            if event.stop_reason == "error":
                await self._safe_send(text="⚠️ Something went wrong on that turn.")
            elif text:
                # Telegram caps message length at 4096 chars.
                for chunk in _chunk_text(text, 4000):
                    await self._safe_send(text=chunk)
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
                text=f"`{event.tool_name}` wants to run. Allow?",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

    async def close(self) -> None:
        self._closed = True

    async def _safe_send(self, **kwargs: Any) -> None:
        try:
            await self._send_message(chat_id=self._chat_id, **kwargs)
        except Exception:
            logger.exception("telegram send failed for chat %s", self._chat_id)


def _chunk_text(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [text]


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
    ) -> None:
        self._token = token
        self._owner_id = owner_id
        self._user_id = user_id
        self._gateway = gateway
        self._approval_router = approval_router
        self._app: Application | None = None
        self._sessions: dict[int, str] = {}
        self._channels: dict[int, TelegramChannel] = {}

    async def start(self) -> None:
        app = Application.builder().token(self._token).build()
        app.add_handler(CommandHandler("start", self._on_start))
        app.add_handler(CommandHandler("new", self._on_new))
        app.add_handler(CallbackQueryHandler(self._on_callback))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )
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
                    self._approval_router.unbind(session_id)
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
            "Pishkar ready. Send a message; /new starts a fresh session."
        )

    async def _on_new(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update) or update.message is None:
            return
        chat_id = update.message.chat_id
        await self._rotate_session(chat_id)
        await update.message.reply_text("Started a fresh session.")

    async def _on_message(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update):
            return
        if update.message is None or not update.message.text:
            return
        chat_id = update.message.chat_id
        session_id = self._ensure_channel(chat_id)
        msg = InboundMessage(
            user_id=self._user_id,
            session_id=session_id,
            channel="telegram",
            content=update.message.text,
        )
        await self._gateway.submit(msg)

    async def _on_callback(
        self, update: Update, _: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_owner(update):
            return
        query = update.callback_query
        if query is None or query.data is None or query.message is None:
            return
        await query.answer()
        try:
            decision_raw, request_id = query.data.split(":", 1)
            decision = ApprovalDecision(decision_raw)
        except ValueError:
            return
        chat_id = query.message.chat_id
        session_id = self._sessions.get(chat_id)
        if session_id is None or self._approval_router is None:
            return
        self._approval_router.resolve(session_id, request_id, decision)
        with contextlib.suppress(Exception):
            label = "Allowed" if decision != ApprovalDecision.DENY else "Denied"
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                text=f"{query.message.text}\n\n→ {label}"
            )

    # ---- session lifecycle ----------------------------------------------

    def _ensure_channel(self, chat_id: int) -> str:
        if chat_id in self._sessions:
            return self._sessions[chat_id]
        return self._open_session(chat_id)

    def _open_session(self, chat_id: int) -> str:
        assert self._app is not None
        session_id = str(uuid4())
        channel = TelegramChannel(
            chat_id=chat_id,
            send_message=self._app.bot.send_message,
        )
        self._sessions[chat_id] = session_id
        self._channels[chat_id] = channel
        self._gateway.register_channel(session_id, channel)
        if self._approval_router is not None:
            self._approval_router.bind(session_id, channel.send_event)
        return session_id

    async def _rotate_session(self, chat_id: int) -> None:
        old_channel = self._channels.pop(chat_id, None)
        old_session = self._sessions.pop(chat_id, None)
        if old_channel is not None and old_session is not None:
            self._gateway.detach_channel(old_session, old_channel)
            if self._approval_router is not None:
                self._approval_router.unbind(old_session)
            await old_channel.close()
        self._open_session(chat_id)


__all__ = ["TelegramBotRunner", "TelegramChannel"]
