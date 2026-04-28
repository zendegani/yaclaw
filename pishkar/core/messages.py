from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

TrustLevel = Literal["full", "limited", "untrusted"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid4())


class InboundMessage(BaseModel):
    message_id: str = Field(default_factory=_uuid)
    user_id: str
    session_id: str
    channel: str
    content: str
    trust_level: TrustLevel = "full"
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_now)


class OutboundMessage(BaseModel):
    message_id: str = Field(default_factory=_uuid)
    user_id: str
    session_id: str
    channel: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_now)


class Turn(BaseModel):
    turn_id: str = Field(default_factory=_uuid)
    session_id: str
    started_at: datetime = Field(default_factory=_now)
    ended_at: datetime | None = None


class Session(BaseModel):
    session_id: str = Field(default_factory=_uuid)
    user_id: str
    created_at: datetime = Field(default_factory=_now)
