"""Webhook trigger — external systems push `InboundMessage`s over HTTP.

The pre-paid `TriggerSource` seam, in its HTTP shape: instead of a
polling loop, this trigger registers a FastAPI route (`POST
/webhook/{name}`) whose handler submits a synthetic message into the
Gateway. GitHub events, Home Assistant automations, IFTTT — anything
that can POST — becomes an input source.

Config lives in `webhooks.json` next to `cron.json` and is re-read on
every request, so edits apply without a restart (the workspace is the
settings UI). Entry shape:

    [
      {
        "name": "gh-ci",
        "user_id": "ali",
        "secret": "a-long-random-string",
        "prompt": "CI finished. Summarize the payload for me.",
        "session_id": "webhook-gh-ci",
        "trust_level": "untrusted"
      }
    ]

`name`, `user_id`, and `secret` are required — an entry without a
secret is ignored entirely rather than exposed unauthenticated. The
caller must send the secret in the `X-Pishkar-Secret` header. `prompt`,
`session_id` (default `webhook-<name>`, one persistent session per
hook), and `trust_level` (default `untrusted` — webhook payloads are
attacker-controllable text) are optional.

The request body (JSON or plain text) is embedded in the message
content, truncated to `PAYLOAD_CAP` characters, and the agent takes it
from there.
"""

import contextlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from pishkar.core.messages import InboundMessage, TrustLevel
from pishkar.triggers.base import Submit

logger = logging.getLogger(__name__)

PAYLOAD_CAP = 8_000

_TRUST_LEVELS: frozenset[str] = frozenset({"full", "limited", "untrusted"})


def load_webhook_config(config_path: Path) -> dict[str, dict[str, Any]]:
    """Read `webhooks.json` and return usable entries keyed by name.

    Malformed files and unusable entries (missing name / user_id /
    secret) yield nothing — a broken config must not open an
    unauthenticated route.
    """
    if not config_path.is_file():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("webhooks: could not parse %s; ignoring", config_path)
        return {}
    if not isinstance(data, list):
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        user_id = entry.get("user_id")
        secret = entry.get("secret")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(user_id, str)
            or not user_id
            or not isinstance(secret, str)
            or not secret
        ):
            logger.warning(
                "webhooks: skipping entry missing name/user_id/secret in %s",
                config_path,
            )
            continue
        entries[name] = entry
    return entries


def _entry_trust(entry: dict[str, Any]) -> TrustLevel:
    raw = entry.get("trust_level", "untrusted")
    if raw not in _TRUST_LEVELS:
        return "untrusted"
    trust: TrustLevel = raw
    return trust


def _payload_text(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    # Pretty-print JSON bodies; anything else is embedded verbatim.
    with contextlib.suppress(json.JSONDecodeError):
        text = json.dumps(json.loads(text), indent=2)
    if len(text) > PAYLOAD_CAP:
        text = text[:PAYLOAD_CAP] + "\n[truncated]"
    return text


def build_webhook_router(submit: Submit, config_path: Path) -> APIRouter:
    router = APIRouter()

    @router.post("/webhook/{name}", status_code=202)
    async def _webhook(name: str, request: Request) -> dict[str, str]:
        entry = load_webhook_config(config_path).get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail="Unknown webhook.")
        provided = request.headers.get("X-Pishkar-Secret", "")
        if not hmac.compare_digest(provided, str(entry["secret"])):
            raise HTTPException(status_code=403, detail="Bad or missing secret.")

        prompt = entry.get("prompt")
        content = (
            prompt if isinstance(prompt, str) and prompt
            else f"Webhook {name!r} fired."
        )
        payload = _payload_text(await request.body())
        if payload:
            content = f"{content}\n\nPayload:\n```\n{payload}\n```"

        session_id = entry.get("session_id")
        msg = InboundMessage(
            user_id=str(entry["user_id"]),
            session_id=(
                session_id if isinstance(session_id, str) and session_id
                else f"webhook-{name}"
            ),
            channel="webhook",
            content=content,
            trust_level=_entry_trust(entry),
            metadata={"webhook_name": name},
        )
        await submit(msg)
        return {"status": "accepted", "message_id": msg.message_id}

    return router


__all__ = ["PAYLOAD_CAP", "build_webhook_router", "load_webhook_config"]
