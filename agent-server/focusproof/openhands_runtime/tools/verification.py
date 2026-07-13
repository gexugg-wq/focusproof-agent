from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from openhands.sdk.tool import Action, Observation
from pydantic import field_validator

VerificationStatus = Literal["success", "failed", "inconclusive", "unsupported"]


class EvidenceReferenceAction(Action):
    evidence_id: str

    @field_validator("evidence_id")
    @classmethod
    def validate_evidence_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence_id must not be empty")
        return value


class VerificationObservation(Observation):
    evidence_id: str
    capability: str
    status: VerificationStatus
    facts: dict[str, Any]
    weak_signals: list[str]
    source_refs: list[str]
    verifier_version: str
    started_at: datetime
    completed_at: datetime
    error_code: str | None = None
    safe_error_message: str | None = None

    @field_validator("evidence_id", "capability", "verifier_version")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("verification identifiers must not be empty")
        return value

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: list[str]) -> list[str]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("source_refs must contain non-empty references")
        return value

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("verification timestamps must be timezone-aware")
        return value.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)
