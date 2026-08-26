from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from focusproof.domain.review import Finding
from focusproof.runtime.actions import Action
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.observations import Observation


class SessionView(BaseModel):
    id: str
    status: str
    startedAt: str | None = None
    endedAt: str | None = None
    elapsedSeconds: int | None = None


class QuestionView(BaseModel):
    questionId: str
    question: str
    reason: str
    relatedEvidenceIds: list[str] = Field(default_factory=list)


class ToolDescription(BaseModel):
    name: str
    description: str
    inputSchema: dict[str, object] = Field(default_factory=dict)


class AgentView(BaseModel):
    session: SessionView
    goal: LearningGoal
    evidence: list[Evidence] = Field(default_factory=list)
    verificationResults: list[Observation] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    unansweredQuestions: list[QuestionView] = Field(default_factory=list)
    availableTools: list[ToolDescription] = Field(default_factory=list)
    previousActions: list[Action] = Field(default_factory=list)
    pluginCapabilities: list[dict[str, Any]] = Field(default_factory=list)
    productCapabilities: list[dict[str, Any]] = Field(default_factory=list)
