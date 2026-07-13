from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from focusproof.domain.review import ReviewResult
from focusproof.runtime.actions import Action
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.observations import Observation


class CreateSessionRequest(BaseModel):
    domain: str
    title: str
    goal: str
    expectedOutput: str | None = None
    plannedMinutes: int | None = None


class SubmitEvidenceRequest(BaseModel):
    evidenceType: str
    textContent: str | None = None
    sourceUrl: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubmitAnswerRequest(BaseModel):
    questionId: str
    answer: str


class DebugConversationTestRequest(BaseModel):
    domain: str
    goal: str
    evidence: str


class SessionRecord(BaseModel):
    sessionId: str
    status: str
    goal: LearningGoal
    evidence: list[Evidence] = Field(default_factory=list)
    answers: dict[str, str] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)
    previousActions: list[Action] = Field(default_factory=list)
    reviewResult: ReviewResult | None = None
    adapterMode: str
