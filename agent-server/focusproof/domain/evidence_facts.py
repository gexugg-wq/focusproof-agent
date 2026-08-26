from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class EvidenceAccessDenied(RuntimeError):
    """The scoped caller is not allowed to see the requested facts."""


class EvidenceFactsNotReady(RuntimeError):
    """The evidence exists, but verified facts are not ready to inspect."""


@dataclass(frozen=True, slots=True)
class LegacyScanProjection:
    attempt_id: str
    scan_result: str
    clean_receipt_id: str | None
    safe_fact_count: int


def normalize_legacy_scan_projection(
    *,
    scan_result: str | None,
    rejection_code: str | None,
    attempt_id: str,
    clean_receipt_id: str | None = None,
) -> LegacyScanProjection:
    normalized = (scan_result or "").strip().lower()
    code = (rejection_code or "").strip().lower()
    if normalized == "unknown":
        normalized = {
            "daemon_unavailable": "unavailable",
            "deadline_exceeded": "timeout",
            "payload_too_large": "oversize",
            "malware_signature_detected": "malicious",
            "daemon_error": "error",
            "legacy_unknown_unclassified": "error",
        }.get(code, "error")
    elif normalized not in {"clean", "malicious", "oversize", "timeout", "unavailable", "error"}:
        normalized = "error"
    receipt = clean_receipt_id.strip() if clean_receipt_id and normalized == "clean" else None
    return LegacyScanProjection(
        attempt_id=attempt_id,
        scan_result=normalized,
        clean_receipt_id=receipt,
        safe_fact_count=1 if receipt is not None else 0,
    )


@dataclass(frozen=True, slots=True)
class MediaEvidenceFacts:
    evidence_id: str
    receipt_id: str
    attempt_id: str
    scan_result: str
    artifact_ref: str
    artifact_sha256: str
    media_type: str
    normalized_sha256: str
    byte_size: int
    width: int
    height: int
    learner_explanation: str

    def __post_init__(self) -> None:
        evidence_id = self.evidence_id.strip()
        receipt_id = self.receipt_id.strip()
        attempt_id = self.attempt_id.strip()
        scan_result = self.scan_result.strip().lower()
        artifact_ref = self.artifact_ref.strip()
        artifact_sha256 = self.artifact_sha256.removeprefix("sha256:").strip().lower()
        media_type = self.media_type.strip().lower()
        normalized_sha256 = self.normalized_sha256.removeprefix("sha256:").strip().lower()
        learner_explanation = " ".join(self.learner_explanation.split())
        if not evidence_id:
            raise ValueError("evidence_id must not be empty")
        if not receipt_id:
            raise ValueError("receipt_id must not be empty")
        if not attempt_id:
            raise ValueError("attempt_id must not be empty")
        if scan_result != "clean":
            raise ValueError("media evidence facts require a clean scan result")
        if not artifact_ref:
            raise ValueError("artifact_ref must not be empty")
        _require_sha256(artifact_sha256, "artifact_sha256")
        if "/" not in media_type:
            raise ValueError("media_type must be a MIME type")
        _require_sha256(normalized_sha256, "normalized_sha256")
        if self.byte_size <= 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("media dimensions and byte size must be positive")
        if not learner_explanation:
            raise ValueError("learner_explanation must not be empty")
        object.__setattr__(self, "evidence_id", evidence_id)
        object.__setattr__(self, "receipt_id", receipt_id)
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(self, "scan_result", scan_result)
        object.__setattr__(self, "artifact_ref", artifact_ref)
        object.__setattr__(self, "artifact_sha256", artifact_sha256)
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "normalized_sha256", normalized_sha256)
        object.__setattr__(self, "learner_explanation", learner_explanation)


class ScopedMediaEvidenceRepository(Protocol):
    def get_media_evidence_facts(
        self,
        session_id: str,
        evidence_id: str,
    ) -> MediaEvidenceFacts: ...


def _require_sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
