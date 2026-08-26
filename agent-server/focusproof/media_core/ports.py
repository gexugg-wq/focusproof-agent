from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import ContextManager, Literal, Protocol, TypeVar, runtime_checkable

from focusproof.contracts.media_scan import MediaScanAuditSnapshot
from focusproof.media_core.models import (
    CleanReceiptPublicationClaim,
    FinalizeMediaOutcome,
    FinalizeMediaRequest,
    IngestedEvidenceResult,
    JSONScalar,
    MediaLease,
    MediaCleanReceipt,
    MediaScanAttempt,
    PendingCleanReceipt,
    MediaReferenceIntent,
    MediaReservationRequest,
    StagedMediaObject,
    freeze_attributes,
)


__all__ = [
    "MediaCancellationGate",
    "MalwareScanner",
    "MalwareScanStatus",
    "MalwareScanVerdict",
    "MediaScanAuditSnapshot",
    "malware_rejection_code",
    "MediaNormalizer",
    "MediaObjectStore",
    "MediaTransactionPort",
    "MediaUnitOfWorkFactory",
    "MediaUnitOfWorkPort",
    "MediaValidator",
    "NormalizedMediaSource",
    "QuarantineStore",
    "QuarantineWriter",
    "ReadOnlyMediaSource",
    "ReadOnlyQuarantineObject",
    "SeekableBinaryIO",
    "StagedMediaObject",
    "ValidatedMediaMetadata",
]


MalwareScanStatus = Literal[
    "clean", "malicious", "oversize", "unavailable", "timeout", "error", "unknown"
]
_VALID_SCAN_STATUSES = frozenset(
    {"clean", "malicious", "oversize", "unavailable", "timeout", "error", "unknown"}
)
_SCAN_REJECTION_CODES: dict[str, str] = {
    "malicious": "media_malware_detected",
    "unavailable": "media_scan_unavailable",
    "oversize": "media_scan_failed",
    "timeout": "media_scan_timeout",
    "error": "media_scan_failed",
    "unknown": "media_scan_unknown",
}


@dataclass(frozen=True, slots=True)
class MalwareScanVerdict:
    status: MalwareScanStatus
    engine: str
    signature_version: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _VALID_SCAN_STATUSES:
            raise ValueError("invalid malware scan status")
        if not self.engine.strip():
            raise ValueError("malware scanner engine must not be blank")
        if self.signature_version is not None and not self.signature_version.strip():
            raise ValueError("signature version must not be blank")


def malware_rejection_code(verdict: MalwareScanVerdict) -> str | None:
    return _SCAN_REJECTION_CODES.get(verdict.status)


_T = TypeVar("_T")


class MediaCancellationGate(Protocol):
    def check_active(self) -> None: ...
    def run_if_active(self, action: Callable[[], _T]) -> _T: ...
    def run_commit(self, action: Callable[[], _T]) -> _T: ...


class SeekableBinaryIO(Protocol):
    def read(self, size: int = -1) -> bytes: ...
    def seek(self, offset: int, whence: int = 0) -> int: ...
    def tell(self) -> int: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ReadOnlyMediaSource:
    stream: SeekableBinaryIO
    byte_size: int
    streaming_sha256: str


@runtime_checkable
class MalwareScanner(Protocol):
    @property
    def audit_snapshot(self) -> MediaScanAuditSnapshot: ...
    def scan(self, source: ReadOnlyMediaSource) -> MalwareScanVerdict: ...


class QuarantineWriter(Protocol):
    def write(self, chunk: bytes) -> None: ...
    def finalize(self) -> ReadOnlyQuarantineObject: ...
    def abort(self) -> None: ...
    def close(self) -> None: ...


class ReadOnlyQuarantineObject(Protocol):
    quarantine_id: str
    receipt_id: str
    receipt_hash: str
    byte_size: int
    streaming_sha256: str
    quarantine_expires_at: datetime

    def open(self) -> ContextManager[SeekableBinaryIO]: ...
    def delete(self) -> None: ...
    def close(self) -> None: ...


class QuarantineStore(Protocol):
    def create(self, reservation_id: str) -> QuarantineWriter: ...
    def create_untrusted_scan_spool(self, reservation_id: str) -> QuarantineWriter: ...
    def pending_clean_receipt(
        self,
        spool: ReadOnlyQuarantineObject,
        *,
        receipt_id: str,
        attempt_id: str,
        artifact_sha256: str,
        receipt_hash: str,
        created_at: datetime,
    ) -> PendingCleanReceipt: ...
    def open_pending_spool(self, pending: PendingCleanReceipt) -> ReadOnlyQuarantineObject: ...
    def open_formal_clean_receipt(
        self, pending: PendingCleanReceipt
    ) -> ReadOnlyQuarantineObject | None: ...
    def find_promoted_clean_receipt(
        self,
        *,
        receipt_id: str,
        receipt_hash: str,
        artifact_sha256: str,
    ) -> ReadOnlyQuarantineObject | None: ...
    def discard_pending_clean_receipt(self, pending: PendingCleanReceipt) -> bool: ...
    def promote_clean_spool(
        self,
        spool: ReadOnlyQuarantineObject,
        *,
        receipt_id: str,
        receipt_hash: str,
        formal_artifact_id: str,
        quarantine_expires_at: datetime | None = None,
    ) -> ReadOnlyQuarantineObject: ...


@dataclass(frozen=True, slots=True)
class ValidatedMediaMetadata:
    media_type: str
    byte_size: int
    source_sha256: str
    attributes: Mapping[str, JSONScalar]

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", freeze_attributes(self.attributes))


class MediaValidator(Protocol):
    def validate(
        self,
        source: ReadOnlyMediaSource,
        declared_media_type: str | None,
    ) -> ValidatedMediaMetadata: ...


class NormalizedMediaSource(Protocol):
    stream: SeekableBinaryIO
    media_type: str
    byte_size: int
    normalized_sha256: str

    def rewind(self) -> None: ...
    def close(self) -> None: ...


class MediaNormalizer(Protocol):
    def normalize(
        self,
        source: ReadOnlyMediaSource,
        metadata: ValidatedMediaMetadata,
    ) -> NormalizedMediaSource: ...


class MediaObjectStore(Protocol):
    def stage(
        self,
        normalized: NormalizedMediaSource,
        media_item_id: str,
        reservation_id: str,
    ) -> StagedMediaObject: ...
    def mark_referenced(self, staged: StagedMediaObject) -> None: ...
    def abort_staged(self, staged: StagedMediaObject) -> None: ...
    def open(self, opaque_object_key: str) -> ContextManager[SeekableBinaryIO]: ...
    def delete(self, opaque_object_key: str) -> None: ...


class MediaTransactionPort(Protocol):
    def reserve(self, request: MediaReservationRequest) -> MediaLease: ...
    def find_idempotent_outcome(
        self,
        owner_id: str,
        session_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> FinalizeMediaOutcome | None: ...
    def finalize(self, request: FinalizeMediaRequest) -> FinalizeMediaOutcome: ...
    def confirm_reference(self, intent: MediaReferenceIntent) -> IngestedEvidenceResult: ...
    def list_pending_reference_outcomes(self, limit: int) -> tuple[FinalizeMediaOutcome, ...]: ...
    def reject(self, lease: MediaLease, reason: str) -> None: ...


class MediaScanAuditPort(Protocol):
    def record_attempt(self, attempt: MediaScanAttempt) -> MediaScanAttempt: ...
    def record_pending_clean_receipt(self, pending: PendingCleanReceipt) -> PendingCleanReceipt: ...
    def record_clean_receipt(self, receipt: MediaCleanReceipt) -> MediaCleanReceipt: ...
    def find_pending_clean_receipt(
        self,
        idempotency_key: str,
    ) -> tuple[MediaScanAttempt, PendingCleanReceipt] | None: ...
    def claim_pending_clean_publication(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CleanReceiptPublicationClaim | None: ...
    def refresh_pending_clean_publication_lease(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> PendingCleanReceipt | None: ...
    def mark_pending_clean_publication_published(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        formal_artifact_id: str,
        now: datetime,
    ) -> PendingCleanReceipt: ...
    def mark_pending_clean_publication_failed(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        now: datetime,
        reason: str,
    ) -> bool: ...
    def list_expired_pending_clean_receipts(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[PendingCleanReceipt, ...]: ...
    def delete_pending_clean_receipt(self, receipt_id: str) -> bool: ...


class MediaUnitOfWorkPort(Protocol):
    media: MediaTransactionPort
    scan_audit: MediaScanAuditPort

    def __enter__(self) -> MediaUnitOfWorkPort: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


class MediaUnitOfWorkFactory(Protocol):
    def __call__(self) -> MediaUnitOfWorkPort: ...
