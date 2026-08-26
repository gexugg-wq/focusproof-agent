from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Literal, TypeAlias

from focusproof.contracts.media_scan import (
    SCAN_RESULT_REJECTION_CODE_CHECK_SQL,
    SCAN_RESULT_REJECTION_CODES,
    MediaScanAuditSnapshot,
    ScanRejectionCode,
    ScanResultKind,
)

JSONScalar: TypeAlias = str | int | float | bool | None

__all__ = [
    "SCAN_RESULT_REJECTION_CODE_CHECK_SQL",
    "SCAN_RESULT_REJECTION_CODES",
    "MediaScanAuditSnapshot",
    "ScanRejectionCode",
    "ScanResultKind",
]

def _require_non_blank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _validate_scan_snapshot(
    *,
    definitions_version: str,
    definitions_age_seconds: int,
    max_bytes: int,
    max_concurrent_scans: int,
    deadline_ms: int,
    socket_timeout_ms: int,
) -> None:
    _require_non_blank(definitions_version, "definitions_version")
    if definitions_age_seconds < 0:
        raise ValueError("definitions_age_seconds must be non-negative")
    for field_name, value in (
        ("max_bytes", max_bytes),
        ("max_concurrent_scans", max_concurrent_scans),
        ("deadline_ms", deadline_ms),
        ("socket_timeout_ms", socket_timeout_ms),
    ):
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")


@dataclass(frozen=True, slots=True)
class MediaScanAttempt:
    attempt_id: str
    artifact_sha256: str
    content_type: str
    scanner_backend: str
    definitions_version: str
    definitions_fresh_at: datetime
    definitions_age_seconds: int
    max_bytes: int
    max_concurrent_scans: int
    deadline_ms: int
    socket_timeout_ms: int
    scan_result: ScanResultKind
    rejection_code: ScanRejectionCode | None
    rejection_detail: str | None
    started_at: datetime
    finished_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        for field_name in (
            "attempt_id",
            "artifact_sha256",
            "content_type",
            "scanner_backend",
            "idempotency_key",
        ):
            _require_non_blank(getattr(self, field_name), field_name)
        if len(self.artifact_sha256) != 64:
            raise ValueError("artifact_sha256 must contain 64 characters")
        _validate_scan_snapshot(
            definitions_version=self.definitions_version,
            definitions_age_seconds=self.definitions_age_seconds,
            max_bytes=self.max_bytes,
            max_concurrent_scans=self.max_concurrent_scans,
            deadline_ms=self.deadline_ms,
            socket_timeout_ms=self.socket_timeout_ms,
        )
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if not isinstance(self.scan_result, ScanResultKind):
            raise ValueError("scan_result must be a frozen ScanResultKind")
        if self.rejection_code is not None and not isinstance(
            self.rejection_code, ScanRejectionCode
        ):
            raise ValueError("rejection_code must be a frozen ScanRejectionCode")
        if self.rejection_code not in SCAN_RESULT_REJECTION_CODES[self.scan_result]:
            raise ValueError(f"rejection_code is invalid for scan_result {self.scan_result.value}")
        if self.scan_result is ScanResultKind.CLEAN and self.rejection_detail is not None:
            raise ValueError("clean scan attempt cannot carry rejection detail")


@dataclass(frozen=True, slots=True)
class MediaCleanReceipt:
    receipt_id: str
    attempt_id: str
    artifact_sha256: str
    receipt_hash: str
    scanner_backend: str
    definitions_version: str
    definitions_fresh_at: datetime
    definitions_age_seconds: int
    max_bytes: int
    max_concurrent_scans: int
    deadline_ms: int
    socket_timeout_ms: int
    quarantine_path: str
    quarantine_expires_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "receipt_id",
            "attempt_id",
            "artifact_sha256",
            "receipt_hash",
            "scanner_backend",
            "quarantine_path",
        ):
            _require_non_blank(getattr(self, field_name), field_name)
        if len(self.artifact_sha256) != 64 or len(self.receipt_hash) != 64:
            raise ValueError("receipt hashes must contain 64 characters")
        _validate_scan_snapshot(
            definitions_version=self.definitions_version,
            definitions_age_seconds=self.definitions_age_seconds,
            max_bytes=self.max_bytes,
            max_concurrent_scans=self.max_concurrent_scans,
            deadline_ms=self.deadline_ms,
            socket_timeout_ms=self.socket_timeout_ms,
        )
        if self.quarantine_expires_at <= self.created_at:
            raise ValueError("quarantine_expires_at must follow created_at")

    @classmethod
    def from_attempt(
        cls,
        attempt: MediaScanAttempt,
        *,
        receipt_id: str,
        receipt_hash: str,
        quarantine_path: str,
        quarantine_expires_at: datetime,
        created_at: datetime,
    ) -> MediaCleanReceipt:
        if attempt.scan_result is not ScanResultKind.CLEAN:
            raise ValueError("clean receipt requires a clean scan attempt")
        return cls(
            receipt_id=receipt_id,
            attempt_id=attempt.attempt_id,
            artifact_sha256=attempt.artifact_sha256,
            receipt_hash=receipt_hash,
            scanner_backend=attempt.scanner_backend,
            definitions_version=attempt.definitions_version,
            definitions_fresh_at=attempt.definitions_fresh_at,
            definitions_age_seconds=attempt.definitions_age_seconds,
            max_bytes=attempt.max_bytes,
            max_concurrent_scans=attempt.max_concurrent_scans,
            deadline_ms=attempt.deadline_ms,
            socket_timeout_ms=attempt.socket_timeout_ms,
            quarantine_path=quarantine_path,
            quarantine_expires_at=quarantine_expires_at,
            created_at=created_at,
        )


@dataclass(frozen=True, slots=True)
class PendingCleanReceipt:
    receipt_id: str
    attempt_id: str
    artifact_sha256: str
    receipt_hash: str
    spool_token: str
    spool_byte_size: int
    spool_sha256: str
    spool_expires_at: datetime
    quarantine_expires_at: datetime
    created_at: datetime
    formal_artifact_id: str = ""
    publication_status: PublicationStatus = "pending"
    publication_owner: str | None = None
    publication_lease_expires_at: datetime | None = None
    publication_version: int = 0
    published_at: datetime | None = None
    publication_failure: str | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.formal_artifact_id:
            object.__setattr__(
                self,
                "formal_artifact_id",
                formal_artifact_id_from_receipt(self.receipt_id, self.receipt_hash),
            )
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.created_at)
        for field_name in (
            "receipt_id",
            "attempt_id",
            "artifact_sha256",
            "receipt_hash",
            "spool_token",
            "spool_sha256",
            "formal_artifact_id",
        ):
            _require_non_blank(getattr(self, field_name), field_name)
        if (
            len(self.artifact_sha256) != 64
            or len(self.receipt_hash) != 64
            or len(self.spool_sha256) != 64
        ):
            raise ValueError("pending receipt hashes must contain 64 characters")
        if len(self.formal_artifact_id) != 32 or any(
            char not in "0123456789abcdef" for char in self.formal_artifact_id
        ):
            raise ValueError("formal_artifact_id must be 32 lowercase hex characters")
        if self.spool_byte_size < 0:
            raise ValueError("pending receipt spool_byte_size must be non-negative")
        if self.spool_expires_at <= self.created_at:
            raise ValueError("pending receipt spool_expires_at must follow created_at")
        if self.quarantine_expires_at <= self.created_at:
            raise ValueError("pending receipt quarantine_expires_at must follow created_at")
        if self.publication_status not in {"pending", "publishing", "published", "failed"}:
            raise ValueError("invalid pending receipt publication status")
        if self.publication_version < 0:
            raise ValueError("publication_version must be non-negative")
        if self.publication_status == "publishing":
            if not self.publication_owner or self.publication_lease_expires_at is None:
                raise ValueError("publishing pending receipt requires an owner and lease")
        elif self.publication_owner is not None or self.publication_lease_expires_at is not None:
            raise ValueError("only publishing pending receipts may carry an owner lease")
        if self.publication_status == "published" and self.published_at is None:
            raise ValueError("published pending receipt requires published_at")
        if self.publication_status != "published" and self.published_at is not None:
            raise ValueError("published_at is only valid for published pending receipts")
        if self.publication_failure is not None and not self.publication_failure.strip():
            raise ValueError("publication_failure must not be blank")


PublicationStatus: TypeAlias = Literal["pending", "publishing", "published", "failed"]


@dataclass(frozen=True, slots=True)
class CleanReceiptPublicationClaim:
    acquired: bool
    pending: PendingCleanReceipt


def formal_artifact_id_from_receipt(receipt_id: str, receipt_hash: str) -> str:
    _require_non_blank(receipt_id, "receipt_id")
    _require_non_blank(receipt_hash, "receipt_hash")
    if len(receipt_hash) != 64:
        raise ValueError("receipt_hash must contain 64 characters")
    return sha256(f"{receipt_id}:{receipt_hash}".encode("utf-8")).hexdigest()[:32]


MediaReservationStatus = Literal[
    "LEASED",
    "RECEIVING",
    "QUARANTINED",
    "VALIDATED",
    "NORMALIZED",
    "STAGED",
    "REFERENCED",
    "REJECTED",
    "ABORTED",
    "EXPIRED",
]


def freeze_attributes(attributes: Mapping[str, JSONScalar] | None = None) -> Mapping[str, JSONScalar]:
    return MappingProxyType(dict(attributes or {}))


@dataclass(frozen=True, slots=True)
class MediaReservationRequest:
    owner_id: str
    session_id: str
    idempotency_key: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class MediaLease:
    reservation_id: str
    media_item_id: str
    owner_id: str
    session_id: str
    slot: int
    idempotency_key: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class StagedMediaObject:
    media_item_id: str
    reservation_id: str
    opaque_object_key: str
    manifest_id: str


@dataclass(frozen=True, slots=True)
class IngestedEvidenceResult:
    evidence_id: str
    media_item_id: str
    artifact_ref: str
    media_type: str
    normalized_sha256: str
    byte_size: int
    learner_explanation: str
    attributes: Mapping[str, JSONScalar]

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", freeze_attributes(self.attributes))


@dataclass(frozen=True, slots=True)
class FinalizeMediaRequest:
    lease: MediaLease
    staged_media_item_id: str
    opaque_object_key: str
    manifest_id: str
    media_type: str
    normalized_sha256: str
    normalized_byte_size: int
    learner_explanation: str
    attributes: Mapping[str, JSONScalar]

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", freeze_attributes(self.attributes))


ReferenceAction: TypeAlias = Literal["MARK_REFERENCED", "ABORT_STAGED", "NOOP"]


@dataclass(frozen=True, slots=True)
class MediaReferenceIntent:
    staged: StagedMediaObject
    action: ReferenceAction


@dataclass(frozen=True, slots=True)
class FinalizeMediaOutcome:
    result: IngestedEvidenceResult
    reference_intent: MediaReferenceIntent
    evidence_visible: bool
