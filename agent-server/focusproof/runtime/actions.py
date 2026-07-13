from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ActionType = Literal[
    "ask_question",
    "request_evidence",
    "verify_evidence",
    "calculate_score",
    "generate_summary",
    "finish_review",
]


class Action(BaseModel):
    type: ActionType
    question: str | None = None
    reason: str | None = None
    relatedEvidenceIds: list[str] = Field(default_factory=list)
    evidenceType: str | None = None
    toolName: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    evidenceIds: list[str] = Field(default_factory=list)
