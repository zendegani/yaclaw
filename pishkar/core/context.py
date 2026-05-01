"""Contextvars carrying per-turn identifiers down through the call stack.

Set by `run_turn` and read by deep collaborators (e.g. the approval
router) that need to know which turn/session they're acting in without
threading IDs through every intermediate signature.
"""

from contextvars import ContextVar

current_turn_id: ContextVar[str] = ContextVar("current_turn_id", default="")
current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")
current_channel: ContextVar[str] = ContextVar("current_channel", default="")

__all__ = ["current_channel", "current_session_id", "current_turn_id"]
