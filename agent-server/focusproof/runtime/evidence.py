from __future__ import annotations

from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field


class LearningGoal(BaseModel):
    domain: str
    title: str
    goal: str
    expectedOutput: str | None = None
    plannedMinutes: int | None = None


class Evidence(BaseModel):
    evidenceId: str
    evidenceType: str
    contentHash: str
    textContent: str | None = None
    sourceUrl: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def hash_evidence_content(text_content: str | None, source_url: str | None) -> str:
    digest = sha256(f"{text_content or ''}|{source_url or ''}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
