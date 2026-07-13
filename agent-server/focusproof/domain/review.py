from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ReviewStatus = Literal[
    "VerifiedLearning",
    "LikelyLearning",
    "WeakEvidence",
    "NeedsMoreVerification",
    "InsufficientEvidence",
    "ContradictoryEvidence",
]


class Finding(BaseModel):
    severity: Literal["info", "warning", "error"] = "info"
    message: str
    evidenceIds: list[str] = Field(default_factory=list)
    observationRefs: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    status: ReviewStatus
    score: int
    confidence: float
    dimensions: dict[str, int]
    findings: list[Finding]
    summary: str
    nextStep: str
