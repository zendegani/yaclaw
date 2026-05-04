"""`speak` tool — render a short message as a voice note in the active
channel. The LLM may call this on its own (e.g. for proactive heartbeat
nudges) or in response to a voice-in turn when it wants to suppress the
default text-and-voice mirror and reply purely in voice.

Falls back to a textual ack when the active channel has no voice output
configured, so calling `speak` is always safe.
"""

from pishkar.core.context import current_session_id
from pishkar.tools.registry import tool
from pishkar.voice import dispatcher


@tool(description=(
    "Render `text` as a voice note in the current channel. Use for short "
    "spoken replies, audible reminders, or when the user clearly wants "
    "voice. Falls through to text when voice output isn't configured."
))
async def speak(text: str) -> str:
    text = text.strip()
    if not text:
        return "Nothing to speak."
    sid = current_session_id.get()
    if not sid:
        return "No active session; cannot speak."
    ok = await dispatcher.speak(sid, text)
    if not ok:
        return "Voice output not configured for this channel."
    preview = text if len(text) <= 200 else text[:200] + "…"
    return f"Spoke: {preview}"


__all__ = ["speak"]
