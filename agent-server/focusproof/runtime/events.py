from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

EventType = Literal[
    "session.created",
    "goal.submitted",
    "session.started",
    "session.paused",
    "session.ended",
    "evidence.submitted",
    "question.asked",
    "answer.submitted",
    "verification.requested",
    "verification.completed",
    "score.calculated",
    "review.completed",
    "proof.record.requested",
    "proof.record.completed",
    "error.occurred",
]
Actor = Literal["user", "agent", "tool", "system"]


class Event(BaseModel):
    id: str = Field(default_factory=lambda: f"evt_{uuid4().hex}")
    sessionId: str
    type: EventType
    sequence: int
    createdAt: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    actor: Actor
    payload: dict[str, Any] = Field(default_factory=dict)
