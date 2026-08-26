from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ObservationStatus = Literal["success", "failed", "inconclusive"]


class Observation(BaseModel):
    toolName: str
    status: ObservationStatus
    sourceEventId: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    sourceRefs: list[str] = Field(default_factory=list)
    error: str | None = None
