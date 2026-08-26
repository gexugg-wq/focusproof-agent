from __future__ import annotations

import ast
import asyncio
import base64
import inspect
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import func, select

from focusproof.media_adapters.local_media_object_store import LocalMediaObjectStore
from focusproof.contracts.media_scan import ScanResultKind, default_scan_rejection_code
from focusproof.media_adapters.local_quarantine_store import LocalQuarantineStore
from focusproof.media_adapters.pillow_image_codec import PillowImageCodecAdapter
from focusproof.media_application import ThreadSafeMediaCancellationGate

from focusproof.media_core.ingestion import (
    CleanupError,
    InvalidMediaReferenceOutcomeError,
    MalwareDetectedError,
    MalwareScanFailedError,
    MalwareScanTimeoutError,
    MalwareScanUnavailableError,
    MediaIngestionService,
    MediaReferencePendingError,
    PostCommitReferenceError,
    QuarantineMetadataMismatchError,
    ValidatedMetadataMismatchError,
)
from focusproof.media_core.limits import MediaQuotaExceeded
from focusproof.media_core.models import (
    CleanReceiptPublicationClaim,
    FinalizeMediaOutcome,
    FinalizeMediaRequest,
    IngestedEvidenceResult,
    JSONScalar,
    MediaLease,
    MediaCleanReceipt,
    MediaScanAttempt,
    MediaReferenceIntent,
    MediaReservationRequest,
    PendingCleanReceipt,
    ReferenceAction,
    StagedMediaObject,
)
from focusproof.media_core.ports import (
    MalwareScanVerdict,
    MediaTransactionPort,
    MediaUnitOfWorkPort,
    NormalizedMediaSource,
    QuarantineWriter,
    ReadOnlyMediaSource,
    ReadOnlyQuarantineObject,
    SeekableBinaryIO,
    ValidatedMediaMetadata,
)
from focusproof.persistence.database import create_database_engine, create_session_factory
from focusproof.persistence.models import (
    AuditEventModel,
    Base,
    EvidenceModel,
    MediaArtifactModel,
    MediaCleanReceiptModel,
    MediaScanAttemptModel,
    PendingCleanReceiptModel,
)
from focusproof.persistence.repositories import StoredSession
from focusproof.persistence.unit_of_work import UnitOfWorkFactory


MiB = 1024 * 1024
MEDIA_TYPE = "application/octet-stream"
PNG_MEDIA_TYPE = "image/png"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_task4_publication_protocol_has_no_sleep_or_filesystem_claims() -> None:
    project_root = Path(__file__).resolve().parents[3]
    legacy_claim_dir = "promotion" + "-claims"
    legacy_claim_attr = "_promo" + "tions"
    legacy_exclusive_flag = "O_" + "EXCL"
    production_files = (
        project_root / "agent-server/focusproof/media_core/ingestion.py",
        project_root / "agent-server/focusproof/media_adapters/local_quarantine_store.py",
    )
    for path in production_files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        sleep_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sleep"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "time"
        ]
        assert sleep_calls == [], f"{path} must not busy-wait with sleep calls"
        assert legacy_claim_dir not in source
        assert legacy_claim_attr not in source
        assert legacy_exclusive_flag not in source


def test_task4_claim_window_harness_uses_two_party_barriers() -> None:
    signature = inspect.signature(PublicationWindowQuarantineStore)

    assert 'publication_ready' in signature.parameters
    assert 'follower_observed' in signature.parameters


@dataclass(slots=True)
class Recorder:
    events: list[str] = field(default_factory=list)
    attempts: list[MediaScanAttempt] = field(default_factory=list)
    pending_receipts: list[PendingCleanReceipt] = field(default_factory=list)
    pending_replay: tuple[MediaScanAttempt, PendingCleanReceipt] | None = None
    receipts: list[MediaCleanReceipt] = field(default_factory=list)
    fail_record_attempt: bool = False
    fail_pending_receipt: bool = False
    fail_active_receipt: bool = False

    def add(self, event: str) -> None:
        self.events.append(event)


class FakeWriter:
    def __init__(
        self,
        recorder: Recorder,
        *,
        kind: str = "quarantine",
        spoof_quarantine: bool = False,
        fail_abort: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.recorder = recorder
        self.kind = kind
        self.spoof_quarantine = spoof_quarantine
        self.fail_abort = fail_abort
        self.fail_close = fail_close
        self.payload = bytearray()

    def write(self, chunk: bytes) -> None:
        self.recorder.add(f"writer.write:{len(chunk)}")
        self.payload.extend(chunk)

    def finalize(self) -> ReadOnlyQuarantineObject:
        self.recorder.add("writer.finalize")
        return FakeQuarantineObject(
            self.recorder,
            bytes(self.payload),
            kind=self.kind,
            spoof_metadata=self.spoof_quarantine,
        )

    def abort(self) -> None:
        self.recorder.add("writer.abort")
        if self.fail_abort:
            raise RuntimeError("abort failed")

    def close(self) -> None:
        self.recorder.add("writer.close")
        if self.fail_close:
            raise RuntimeError("writer close failed")


class FakeQuarantineObject:
    def __init__(
        self,
        recorder: Recorder,
        payload: bytes,
        *,
        kind: str = "quarantine",
        quarantine_id: str = "quarantine-1",
        receipt_id: str = "receipt-1",
        receipt_hash: str = "a" * 64,
        spoof_metadata: bool = False,
        fail_delete: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.recorder = recorder
        self.payload = payload
        self.kind = kind
        self.quarantine_id = quarantine_id
        self.receipt_id = receipt_id
        self.receipt_hash = receipt_hash
        self.byte_size = len(payload) + (1 if spoof_metadata else 0)
        self.streaming_sha256 = sha256(payload).hexdigest()
        self.quarantine_expires_at = datetime.now(UTC) + timedelta(hours=1)
        if spoof_metadata:
            self.streaming_sha256 = "0" * 64
        self.fail_delete = fail_delete
        self.fail_close = fail_close

    @contextmanager
    def open(self) -> Iterator[BytesIO]:
        self.recorder.add(f"{self.kind}.open")
        stream = BytesIO(self.payload)
        try:
            yield stream
        finally:
            stream.close()
            self.recorder.add(f"{self.kind}.stream.close")

    def delete(self) -> None:
        self.recorder.add(f"{self.kind}.delete")
        if self.fail_delete:
            raise RuntimeError("delete failed")

    def close(self) -> None:
        self.recorder.add(f"{self.kind}.close")
        if self.fail_close:
            raise RuntimeError("quarantine close failed")


class FakeQuarantineStore:
    def __init__(
        self,
        recorder: Recorder,
        *,
        fail_create: bool = False,
        fail_pending_receipt: bool = False,
        fail_promote: bool = False,
        spoof_quarantine: bool = False,
        fail_abort: bool = False,
        fail_writer_close: bool = False,
        replay_spool_payload: bytes = b"opaque source",
    ) -> None:
        self.recorder = recorder
        self.fail_create = fail_create
        self.fail_pending_receipt = fail_pending_receipt
        self.fail_promote = fail_promote
        self.spoof_quarantine = spoof_quarantine
        self.fail_abort = fail_abort
        self.fail_writer_close = fail_writer_close
        self.promoted: FakeQuarantineObject | None = None
        self.replay_spool_payload = replay_spool_payload

    def create(
        self,
        reservation_id: str,
        *,
        receipt_id: str = "receipt-1",
        receipt_hash: str = "a" * 64,
    ) -> QuarantineWriter:
        self.recorder.add(f"quarantine.create:{reservation_id}:{receipt_id}:{receipt_hash}")
        if self.fail_create:
            raise RuntimeError("create failed")
        return FakeWriter(
            self.recorder,
            kind="quarantine",
            spoof_quarantine=self.spoof_quarantine,
            fail_abort=self.fail_abort,
            fail_close=self.fail_writer_close,
        )

    def create_untrusted_scan_spool(self, reservation_id: str) -> QuarantineWriter:
        self.recorder.add(f"spool.create:{reservation_id}")
        if self.fail_create:
            raise RuntimeError("create failed")
        return FakeWriter(
            self.recorder,
            kind="spool",
            spoof_quarantine=self.spoof_quarantine,
            fail_abort=self.fail_abort,
            fail_close=self.fail_writer_close,
        )

    def promote_clean_spool(
        self,
        spool: ReadOnlyQuarantineObject,
        *,
        receipt_id: str,
        receipt_hash: str,
        formal_artifact_id: str,
        quarantine_expires_at: datetime | None = None,
    ) -> ReadOnlyQuarantineObject:
        self.recorder.add(f"quarantine.promote:{receipt_id}:{receipt_hash}")
        if self.fail_promote:
            raise RuntimeError("promote failed")
        with spool.open() as stream:
            payload = stream.read()
        self.promoted = FakeQuarantineObject(
            self.recorder,
            payload,
            kind="quarantine",
            quarantine_id=formal_artifact_id,
            receipt_id=receipt_id,
            receipt_hash=receipt_hash,
        )
        if quarantine_expires_at is not None:
            self.promoted.quarantine_expires_at = quarantine_expires_at
        return self.promoted

    def pending_clean_receipt(
        self,
        spool: ReadOnlyQuarantineObject,
        *,
        receipt_id: str,
        attempt_id: str,
        artifact_sha256: str,
        receipt_hash: str,
        created_at: datetime,
    ) -> PendingCleanReceipt:
        self.recorder.add(f"quarantine.pending_capability:{spool.quarantine_id}")
        return PendingCleanReceipt(
            receipt_id=receipt_id,
            attempt_id=attempt_id,
            artifact_sha256=artifact_sha256,
            receipt_hash=receipt_hash,
            spool_token=spool.quarantine_id,
            spool_byte_size=spool.byte_size,
            spool_sha256=spool.streaming_sha256,
            spool_expires_at=spool.quarantine_expires_at,
            quarantine_expires_at=created_at + timedelta(hours=1),
            created_at=created_at,
        )

    def open_pending_spool(self, pending: PendingCleanReceipt) -> ReadOnlyQuarantineObject:
        self.recorder.add(f"spool.open_pending:{pending.spool_token}")
        return FakeQuarantineObject(
            self.recorder,
            self.replay_spool_payload,
            kind="spool",
        )

    def open_formal_clean_receipt(
        self,
        pending: PendingCleanReceipt,
    ) -> ReadOnlyQuarantineObject | None:
        self.recorder.add(f"quarantine.open_formal:{pending.formal_artifact_id}")
        if (
            self.promoted is not None
            and self.promoted.quarantine_id == pending.formal_artifact_id
            and self.promoted.receipt_id == pending.receipt_id
            and self.promoted.receipt_hash == pending.receipt_hash
            and self.promoted.streaming_sha256 == pending.artifact_sha256
            and self.promoted.byte_size == pending.spool_byte_size
            and self.promoted.quarantine_expires_at == pending.quarantine_expires_at
        ):
            return self.promoted
        return None

    def find_promoted_clean_receipt(
        self,
        *,
        receipt_id: str,
        receipt_hash: str,
        artifact_sha256: str,
    ) -> ReadOnlyQuarantineObject | None:
        self.recorder.add(f"quarantine.find_promoted:{receipt_id}:{receipt_hash}")
        if (
            self.promoted is not None
            and self.promoted.receipt_id == receipt_id
            and self.promoted.receipt_hash == receipt_hash
            and self.promoted.streaming_sha256 == artifact_sha256
        ):
            return self.promoted
        return None


@dataclass(frozen=True, slots=True)
class FakeScanSnapshot:
    scanner_backend: str = "test-engine"
    definitions_version: str = "test-definitions-nondefault"
    definitions_fresh_at: datetime = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    definitions_age_seconds: int = 42
    max_bytes: int = 12_345_678
    max_concurrent_scans: int = 7
    deadline_ms: int = 9_876
    socket_timeout_ms: int = 543


class FakeMalwareScanner:
    def __init__(
        self,
        recorder: Recorder,
        *,
        status: str = "clean",
        raises: BaseException | None = None,
        snapshot: FakeScanSnapshot | None = None,
    ) -> None:
        self.recorder = recorder
        self.status = status
        self.raises = raises
        self.audit_snapshot = snapshot or FakeScanSnapshot()

    def scan(self, source: ReadOnlyMediaSource) -> MalwareScanVerdict:
        self.recorder.add(f"scanner.scan:{source.byte_size}:{source.streaming_sha256}")
        source.stream.read()
        if self.raises is not None:
            raise self.raises
        return MalwareScanVerdict(status=self.status, engine="test-engine")  # type: ignore[arg-type]


class FakeValidator:
    def __init__(
        self,
        recorder: Recorder,
        *,
        attributes: Mapping[str, JSONScalar] | None = None,
        spoof_validated_metadata: bool = False,
    ) -> None:
        self.recorder = recorder
        default_attributes: Mapping[str, JSONScalar] = {"codec_note": "kept", "ordinal": 7}
        self.attributes = dict(attributes or default_attributes)
        self.spoof_validated_metadata = spoof_validated_metadata

    def validate(
        self,
        source: ReadOnlyMediaSource,
        declared_media_type: str | None,
    ) -> ValidatedMediaMetadata:
        self.recorder.add(f"validator.validate:{declared_media_type}:{source.streaming_sha256}")
        source.stream.read()
        return ValidatedMediaMetadata(
            media_type=MEDIA_TYPE,
            byte_size=source.byte_size + (1 if self.spoof_validated_metadata else 0),
            source_sha256="0" * 64 if self.spoof_validated_metadata else source.streaming_sha256,
            attributes=self.attributes,
        )


class FakeNormalizedSource:
    media_type = MEDIA_TYPE
    stream: SeekableBinaryIO

    def __init__(
        self,
        recorder: Recorder,
        payload: bytes,
        *,
        bad_metadata: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.recorder = recorder
        self.stream = BytesIO(payload)
        self.byte_size = len(payload) + (1 if bad_metadata else 0)
        self.normalized_sha256 = sha256(payload).hexdigest()
        self.fail_close = fail_close

    def rewind(self) -> None:
        self.recorder.add("normalized.rewind")
        self.stream.seek(0)

    def close(self) -> None:
        self.recorder.add("normalized.close")
        if self.fail_close:
            raise RuntimeError("normalized close failed")
        self.stream.close()


class FakeNormalizer:
    def __init__(
        self,
        recorder: Recorder,
        payload: bytes,
        *,
        bad_metadata: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.recorder = recorder
        self.payload = payload
        self.bad_metadata = bad_metadata
        self.fail_close = fail_close

    def normalize(
        self,
        source: ReadOnlyMediaSource,
        metadata: ValidatedMediaMetadata,
    ) -> NormalizedMediaSource:
        self.recorder.add(f"normalizer.normalize:{metadata.source_sha256}")
        source.stream.read()
        return FakeNormalizedSource(
            self.recorder,
            self.payload,
            bad_metadata=self.bad_metadata,
            fail_close=self.fail_close,
        )


class FakeObjectStore:
    def __init__(
        self,
        recorder: Recorder,
        *,
        fail_abort_staged: bool = False,
        fail_mark_referenced: bool = False,
    ) -> None:
        self.recorder = recorder
        self.fail_abort_staged = fail_abort_staged
        self.fail_mark_referenced = fail_mark_referenced
        self.stage_positions: list[int] = []

    def stage(
        self,
        normalized: NormalizedMediaSource,
        media_item_id: str,
        reservation_id: str,
    ) -> StagedMediaObject:
        self.recorder.add(f"object.stage:{media_item_id}:{reservation_id}")
        self.stage_positions.append(normalized.stream.tell())
        normalized.stream.read()
        return StagedMediaObject(
            media_item_id=media_item_id,
            reservation_id=reservation_id,
            opaque_object_key="opaque-key",
            manifest_id="manifest-1",
        )

    def mark_referenced(self, staged: StagedMediaObject) -> None:
        self.recorder.add(f"object.mark_referenced:{staged.media_item_id}")
        if self.fail_mark_referenced:
            raise RuntimeError("mark failed")

    def abort_staged(self, staged: StagedMediaObject) -> None:
        self.recorder.add(f"object.abort_staged:{staged.media_item_id}")
        if self.fail_abort_staged:
            raise RuntimeError("abort staged failed")

    @contextmanager
    def open(self, opaque_object_key: str) -> Iterator[BytesIO]:
        yield BytesIO(b"")

    def delete(self, opaque_object_key: str) -> None:
        self.recorder.add(f"object.delete:{opaque_object_key}")


@dataclass(slots=True)
class FakeMediaTransactions:
    recorder: Recorder
    committed_count: int = 0
    committed_distinct_bytes: int = 0
    replay: FinalizeMediaOutcome | None = None
    fail_final: bool = False
    fail_reject: bool = False
    fail_confirm: bool = False
    reference_action: ReferenceAction = "MARK_REFERENCED"
    evidence_visible: bool | None = None
    result_media_item_id: str | None = None
    intent_media_item_id: str | None = None
    intent_reservation_id: str | None = None
    intent_opaque_object_key: str | None = None
    intent_manifest_id: str | None = None
    leases: list[MediaLease] = field(default_factory=list)
    final_requests: list[FinalizeMediaRequest] = field(default_factory=list)
    confirm_intents: list[MediaReferenceIntent] = field(default_factory=list)
    reject_reasons: list[str] = field(default_factory=list)

    def reserve(self, request: MediaReservationRequest) -> MediaLease:
        self.recorder.add(f"tx.reserve:{request.owner_id}:{request.session_id}")
        if self.committed_count + len(self.leases) + 1 > 4:
            raise MediaQuotaExceeded("media item count quota exceeded")
        lease = MediaLease(
            reservation_id=f"reservation-{len(self.leases) + 1}",
            media_item_id=f"media-item-{len(self.leases) + 1}",
            owner_id=request.owner_id,
            session_id=request.session_id,
            slot=len(self.leases),
            idempotency_key=request.idempotency_key,
            fingerprint=request.fingerprint,
        )
        self.leases.append(lease)
        return lease

    def find_idempotent_outcome(
        self,
        owner_id: str,
        session_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> FinalizeMediaOutcome | None:
        self.recorder.add(f"tx.replay:{owner_id}:{session_id}:{idempotency_key}:{fingerprint}")
        return self.replay

    def finalize(self, request: FinalizeMediaRequest) -> FinalizeMediaOutcome:
        self.recorder.add(f"tx.finalize:{request.normalized_sha256}:{request.normalized_byte_size}")
        self.final_requests.append(request)
        if self.fail_final or self.committed_distinct_bytes + request.normalized_byte_size > 20 * MiB:
            raise MediaQuotaExceeded("normalized distinct byte quota exceeded")
        intent = MediaReferenceIntent(
            staged=StagedMediaObject(
                media_item_id=self.intent_media_item_id or request.staged_media_item_id,
                reservation_id=self.intent_reservation_id or request.lease.reservation_id,
                opaque_object_key=self.intent_opaque_object_key or request.opaque_object_key,
                manifest_id=self.intent_manifest_id or request.manifest_id,
            ),
            action=self.reference_action,
        )
        result_media_item_id = self.result_media_item_id
        if result_media_item_id is None:
            result_media_item_id = (
                request.staged_media_item_id
                if self.reference_action == "MARK_REFERENCED"
                else "media-item-existing"
            )
        artifact_ref = f"focusproof-artifact://{result_media_item_id}"
        result = IngestedEvidenceResult(
            evidence_id="evidence-1",
            media_item_id=result_media_item_id,
            artifact_ref=artifact_ref,
            media_type=request.media_type,
            normalized_sha256=request.normalized_sha256,
            byte_size=request.normalized_byte_size,
            learner_explanation=request.learner_explanation,
            attributes=request.attributes,
        )
        evidence_visible = self.evidence_visible
        if evidence_visible is None:
            evidence_visible = self.reference_action != "MARK_REFERENCED"
        return FinalizeMediaOutcome(
            result=result,
            reference_intent=intent,
            evidence_visible=evidence_visible,
        )

    def confirm_reference(self, intent: MediaReferenceIntent) -> IngestedEvidenceResult:
        self.recorder.add(f"tx.confirm:{intent.staged.media_item_id}:{intent.action}")
        self.confirm_intents.append(intent)
        if self.fail_confirm:
            raise RuntimeError("confirm failed")
        if self.replay is not None:
            return self.replay.result
        if not self.final_requests:
            return IngestedEvidenceResult(
                evidence_id="evidence-1",
                media_item_id=intent.staged.media_item_id,
                artifact_ref=f"focusproof-artifact://{intent.staged.media_item_id}",
                media_type=MEDIA_TYPE,
                normalized_sha256="hash",
                byte_size=1,
                learner_explanation="confirmed replay",
                attributes={},
            )
        request = self.final_requests[-1]
        return IngestedEvidenceResult(
            evidence_id="evidence-1",
            media_item_id=intent.staged.media_item_id,
            artifact_ref=f"focusproof-artifact://{intent.staged.media_item_id}",
            media_type=request.media_type,
            normalized_sha256=request.normalized_sha256,
            byte_size=request.normalized_byte_size,
            learner_explanation=request.learner_explanation,
            attributes=request.attributes,
        )

    def list_pending_reference_outcomes(self, limit: int) -> tuple[FinalizeMediaOutcome, ...]:
        self.recorder.add(f"tx.list_pending:{limit}")
        if self.replay is None or limit < 1:
            return ()
        return (self.replay,)

    def reject(self, lease: MediaLease, reason: str) -> None:
        self.recorder.add(f"tx.reject:{lease.reservation_id}:{reason}")
        self.reject_reasons.append(reason)
        if self.fail_reject:
            raise RuntimeError("reject failed")


class FakeUow:
    def __init__(self, media: FakeMediaTransactions, recorder: Recorder) -> None:
        self.media: MediaTransactionPort = media
        self.scan_audit = FakeScanAudit(recorder)
        self.recorder = recorder

    def __enter__(self) -> MediaUnitOfWorkPort:
        self.recorder.add("uow.enter")
        return self

    def commit(self) -> None:
        self.recorder.add("uow.commit")

    def rollback(self) -> None:
        self.recorder.add("uow.rollback")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.recorder.add("uow.exit")


class FakeUowFactory:
    def __init__(self, media: FakeMediaTransactions, recorder: Recorder) -> None:
        self.media = media
        self.recorder = recorder

    def __call__(self) -> MediaUnitOfWorkPort:
        return FakeUow(self.media, self.recorder)


class FakeScanAudit:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def record_attempt(self, attempt: MediaScanAttempt) -> MediaScanAttempt:
        self.recorder.add(f"scan_attempt.record:{attempt.attempt_id}:{attempt.scan_result.value}")
        if self.recorder.fail_record_attempt:
            raise RuntimeError("audit write failed")
        self.recorder.attempts.append(attempt)
        return attempt

    def record_pending_clean_receipt(self, pending: PendingCleanReceipt) -> PendingCleanReceipt:
        self.recorder.add(f"clean_receipt.pending:{pending.receipt_id}:{pending.attempt_id}")
        if self.recorder.fail_pending_receipt:
            raise RuntimeError("pending receipt failed")
        self.recorder.pending_receipts.append(pending)
        return pending

    def record_clean_receipt(self, receipt: MediaCleanReceipt) -> MediaCleanReceipt:
        self.recorder.add(f"clean_receipt.record:{receipt.receipt_id}:{receipt.attempt_id}")
        if self.recorder.fail_active_receipt:
            raise RuntimeError("active receipt failed")
        self.recorder.receipts.append(receipt)
        return receipt

    def find_pending_clean_receipt(
        self,
        idempotency_key: str,
    ) -> tuple[MediaScanAttempt, PendingCleanReceipt] | None:
        self.recorder.add(f"clean_receipt.pending_replay:{idempotency_key}")
        if (
            self.recorder.pending_replay is not None
            and self.recorder.pending_replay[0].idempotency_key == idempotency_key
        ):
            return self.recorder.pending_replay
        return None

    def _stored_pending(self, receipt_id: str) -> PendingCleanReceipt | None:
        for pending in reversed(self.recorder.pending_receipts):
            if pending.receipt_id == receipt_id:
                return pending
        if (
            self.recorder.pending_replay is not None
            and self.recorder.pending_replay[1].receipt_id == receipt_id
        ):
            return self.recorder.pending_replay[1]
        return None

    def _replace_pending(self, pending: PendingCleanReceipt) -> PendingCleanReceipt:
        for index, existing in enumerate(self.recorder.pending_receipts):
            if existing.receipt_id == pending.receipt_id:
                self.recorder.pending_receipts[index] = pending
                break
        if (
            self.recorder.pending_replay is not None
            and self.recorder.pending_replay[1].receipt_id == pending.receipt_id
        ):
            self.recorder.pending_replay = (self.recorder.pending_replay[0], pending)
        return pending

    def claim_pending_clean_publication(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CleanReceiptPublicationClaim | None:
        self.recorder.add(f"clean_receipt.publication_claim:{receipt_id}:{owner_token}")
        pending = self._stored_pending(receipt_id)
        if pending is None:
            return None
        if pending.publication_status == "published":
            return CleanReceiptPublicationClaim(acquired=False, pending=pending)
        if pending.publication_status == "publishing" and (
            pending.publication_lease_expires_at is None
            or pending.publication_lease_expires_at > now
        ):
            return CleanReceiptPublicationClaim(acquired=False, pending=pending)
        updated = replace(
            pending,
            publication_status="publishing",
            publication_owner=owner_token,
            publication_lease_expires_at=lease_expires_at,
            publication_version=pending.publication_version + 1,
            publication_failure=None,
            updated_at=now,
        )
        return CleanReceiptPublicationClaim(acquired=True, pending=self._replace_pending(updated))

    def refresh_pending_clean_publication_lease(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> PendingCleanReceipt | None:
        self.recorder.add(f"clean_receipt.publication_refresh:{receipt_id}:{owner_token}")
        pending = self._stored_pending(receipt_id)
        if (
            pending is None
            or pending.publication_status != "publishing"
            or pending.publication_owner != owner_token
        ):
            return None
        return self._replace_pending(
            replace(
                pending,
                publication_lease_expires_at=lease_expires_at,
                publication_version=pending.publication_version + 1,
                updated_at=now,
            )
        )

    def mark_pending_clean_publication_published(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        formal_artifact_id: str,
        now: datetime,
    ) -> PendingCleanReceipt:
        self.recorder.add(f"clean_receipt.publication_published:{receipt_id}:{owner_token}")
        pending = self._stored_pending(receipt_id)
        if pending is None:
            raise RuntimeError("pending receipt missing")
        return self._replace_pending(
            replace(
                pending,
                publication_status="published",
                publication_owner=None,
                publication_lease_expires_at=None,
                publication_version=pending.publication_version + 1,
                published_at=now,
                publication_failure=None,
                updated_at=now,
                formal_artifact_id=formal_artifact_id,
            )
        )

    def mark_pending_clean_publication_failed(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        now: datetime,
        reason: str,
    ) -> bool:
        self.recorder.add(f"clean_receipt.publication_failed:{receipt_id}:{owner_token}")
        pending = self._stored_pending(receipt_id)
        if (
            pending is None
            or pending.publication_status != "publishing"
            or pending.publication_owner != owner_token
        ):
            return False
        self._replace_pending(
            replace(
                pending,
                publication_status="failed",
                publication_owner=None,
                publication_lease_expires_at=None,
                publication_version=pending.publication_version + 1,
                publication_failure=reason,
                updated_at=now,
            )
        )
        return True


def make_service(
    recorder: Recorder,
    *,
    payload: bytes = b"normalized",
    replay: FinalizeMediaOutcome | None = None,
    committed_count: int = 0,
    committed_distinct_bytes: int = 0,
    bad_normalized_metadata: bool = False,
    fail_create: bool = False,
    spoof_quarantine: bool = False,
    fail_abort: bool = False,
    fail_writer_close: bool = False,
    fail_promote: bool = False,
    fail_normalized_close: bool = False,
    fail_abort_staged: bool = False,
    fail_mark_referenced: bool = False,
    fail_final: bool = False,
    fail_reject: bool = False,
    fail_confirm: bool = False,
    reference_action: ReferenceAction = "MARK_REFERENCED",
    evidence_visible: bool | None = None,
    result_media_item_id: str | None = None,
    intent_media_item_id: str | None = None,
    intent_reservation_id: str | None = None,
    intent_opaque_object_key: str | None = None,
    intent_manifest_id: str | None = None,
    attributes: Mapping[str, JSONScalar] | None = None,
    spoof_validated_metadata: bool = False,
    scanner_status: str = "clean",
    scanner_raises: BaseException | None = None,
    scanner_snapshot: FakeScanSnapshot | None = None,
    fail_record_attempt: bool = False,
    fail_pending_receipt: bool = False,
    fail_active_receipt: bool = False,
    cleanup_errors: list[CleanupError] | None = None,
) -> tuple[MediaIngestionService, FakeMediaTransactions, FakeObjectStore]:
    reporter = cleanup_errors.append if cleanup_errors is not None else (lambda error: None)
    recorder.fail_record_attempt = fail_record_attempt
    recorder.fail_pending_receipt = fail_pending_receipt
    recorder.fail_active_receipt = fail_active_receipt
    media = FakeMediaTransactions(
        recorder,
        committed_count=committed_count,
        committed_distinct_bytes=committed_distinct_bytes,
        replay=replay,
        fail_final=fail_final,
        fail_reject=fail_reject,
        fail_confirm=fail_confirm,
        reference_action=reference_action,
        evidence_visible=evidence_visible,
        result_media_item_id=result_media_item_id,
        intent_media_item_id=intent_media_item_id,
        intent_reservation_id=intent_reservation_id,
        intent_opaque_object_key=intent_opaque_object_key,
        intent_manifest_id=intent_manifest_id,
    )
    object_store = FakeObjectStore(
        recorder,
        fail_abort_staged=fail_abort_staged,
        fail_mark_referenced=fail_mark_referenced,
    )
    return (
        MediaIngestionService(
            uow_factory=FakeUowFactory(media, recorder),
            quarantine_store=FakeQuarantineStore(
                recorder,
                fail_create=fail_create,
                fail_promote=fail_promote,
                spoof_quarantine=spoof_quarantine,
                fail_abort=fail_abort,
                fail_writer_close=fail_writer_close,
            ),
            malware_scanner=FakeMalwareScanner(
                recorder,
                status=scanner_status,
                raises=scanner_raises,
                snapshot=scanner_snapshot,
            ),
            validator=FakeValidator(
                recorder,
                attributes=attributes,
                spoof_validated_metadata=spoof_validated_metadata,
            ),
            normalizer=FakeNormalizer(
                recorder,
                payload,
                bad_metadata=bad_normalized_metadata,
                fail_close=fail_normalized_close,
            ),
            object_store=object_store,
            cleanup_error_reporter=reporter,
        ),
        media,
        object_store,
    )


class PublicationWindowQuarantineStore(LocalQuarantineStore):
    def __init__(
        self,
        root: Path,
        *,
        publication_ready: Barrier,
        follower_observed: Barrier,
        owner_reached: Event,
    ) -> None:
        super().__init__(root)
        self._publication_ready = publication_ready
        self._follower_observed = follower_observed
        self._owner_reached = owner_reached
        self._window_opened = False

    def open_formal_clean_receipt(
        self,
        pending: PendingCleanReceipt,
    ) -> ReadOnlyQuarantineObject | None:
        if not self._window_opened:
            self._window_opened = True
            self._owner_reached.set()
            self._publication_ready.wait(timeout=10)
            self._follower_observed.wait(timeout=10)
        return super().open_formal_clean_receipt(pending)


class ValidationBarrierCodec:
    def __init__(
        self,
        delegate: PillowImageCodecAdapter,
        barrier: Barrier,
        reached: Event,
    ) -> None:
        self._delegate = delegate
        self._barrier = barrier
        self._reached = reached

    def validate(
        self,
        source: ReadOnlyMediaSource,
        declared_media_type: str | None,
    ) -> ValidatedMediaMetadata:
        self._reached.set()
        self._barrier.wait(timeout=10)
        return self._delegate.validate(source, declared_media_type)

    def normalize(
        self,
        source: ReadOnlyMediaSource,
        metadata: ValidatedMediaMetadata,
    ) -> NormalizedMediaSource:
        return self._delegate.normalize(source, metadata)


class StageBarrierObjectStore(LocalMediaObjectStore):
    def __init__(self, root: Path, barrier: Barrier, reached: Event) -> None:
        super().__init__(root)
        self._barrier = barrier
        self._reached = reached

    def stage(
        self,
        normalized: NormalizedMediaSource,
        media_item_id: str,
        reservation_id: str,
    ) -> StagedMediaObject:
        self._reached.set()
        self._barrier.wait(timeout=10)
        return super().stage(normalized, media_item_id, reservation_id)


@dataclass(slots=True)
class MarkReferencedRace:
    owner_mark_ready: Event = field(default_factory=Event)
    follower_finalize_waiting: Event = field(default_factory=Event)
    follower_manifest_read: Event = field(default_factory=Event)
    owner_completed: Event = field(default_factory=Event)
    owner_staged: StagedMediaObject | None = None


class OwnerMarkRaceObjectStore(LocalMediaObjectStore):
    def __init__(
        self,
        root: Path,
        stage_barrier: Barrier,
        stage_reached: Event,
        race: MarkReferencedRace,
    ) -> None:
        super().__init__(root)
        self._stage_barrier = stage_barrier
        self._stage_reached = stage_reached
        self._race = race

    def stage(
        self,
        normalized: NormalizedMediaSource,
        media_item_id: str,
        reservation_id: str,
    ) -> StagedMediaObject:
        self._stage_reached.set()
        self._stage_barrier.wait(timeout=10)
        return super().stage(normalized, media_item_id, reservation_id)

    def mark_referenced(self, staged: StagedMediaObject) -> None:
        self._race.owner_staged = staged
        self._race.owner_mark_ready.set()
        if not self._race.follower_manifest_read.wait(timeout=10):
            raise AssertionError("follower did not reach stale manifest read")
        super().mark_referenced(staged)


class FollowerManifestRaceObjectStore(LocalMediaObjectStore):
    def __init__(
        self,
        root: Path,
        stage_barrier: Barrier,
        stage_reached: Event,
        race: MarkReferencedRace,
    ) -> None:
        super().__init__(root)
        self._stage_barrier = stage_barrier
        self._stage_reached = stage_reached
        self._race = race

    def stage(
        self,
        normalized: NormalizedMediaSource,
        media_item_id: str,
        reservation_id: str,
    ) -> StagedMediaObject:
        self._stage_reached.set()
        self._stage_barrier.wait(timeout=10)
        return super().stage(normalized, media_item_id, reservation_id)

    def _assert_manifest(self, staged: StagedMediaObject, *, phase: str) -> Path:
        if (
            phase == "STAGED"
            and self._race.owner_staged == staged
            and not self._race.follower_manifest_read.is_set()
        ):
            self._race.follower_manifest_read.set()
            if not self._race.owner_completed.wait(timeout=10):
                raise AssertionError("owner did not complete durable reference")
        return super()._assert_manifest(staged, phase=phase)


class FinalizeBarrierMediaPort:
    def __init__(
        self,
        delegate: MediaTransactionPort,
        barrier: Barrier,
        reached: Event,
    ) -> None:
        self._delegate = delegate
        self._barrier = barrier
        self._reached = reached

    def reserve(self, request: MediaReservationRequest) -> MediaLease:
        return self._delegate.reserve(request)

    def find_idempotent_outcome(
        self,
        owner_id: str,
        session_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> FinalizeMediaOutcome | None:
        return self._delegate.find_idempotent_outcome(
            owner_id,
            session_id,
            idempotency_key,
            fingerprint,
        )

    def finalize(self, request: FinalizeMediaRequest) -> FinalizeMediaOutcome:
        self._reached.set()
        self._barrier.wait(timeout=10)
        return self._delegate.finalize(request)

    def confirm_reference(self, intent: MediaReferenceIntent) -> IngestedEvidenceResult:
        return self._delegate.confirm_reference(intent)

    def list_pending_reference_outcomes(
        self,
        limit: int,
    ) -> tuple[FinalizeMediaOutcome, ...]:
        return self._delegate.list_pending_reference_outcomes(limit)

    def reject(self, lease: MediaLease, reason: str) -> None:
        self._delegate.reject(lease, reason)


class FinalizeBarrierUowFactory:
    def __init__(self, delegate: UnitOfWorkFactory, barrier: Barrier, reached: Event) -> None:
        self._delegate = delegate
        self._barrier = barrier
        self._reached = reached

    def __call__(self) -> object:
        return FinalizeBarrierUow(self._delegate(), self._barrier, self._reached)


class FinalizeBarrierUow:
    def __init__(self, delegate: object, barrier: Barrier, reached: Event) -> None:
        self._delegate = delegate
        self._barrier = barrier
        self._reached = reached
        self._entered: object | None = None
        self._media: MediaTransactionPort | None = None

    def __enter__(self) -> object:
        self._entered = self._delegate.__enter__()  # type: ignore[attr-defined]
        self._media = FinalizeBarrierMediaPort(
            self._entered.media,  # type: ignore[attr-defined]
            self._barrier,
            self._reached,
        )
        return self

    @property
    def media(self) -> MediaTransactionPort:
        assert self._media is not None
        return self._media

    @property
    def scan_audit(self) -> object:
        assert self._entered is not None
        return self._entered.scan_audit  # type: ignore[attr-defined]

    def commit(self) -> None:
        assert self._entered is not None
        self._entered.commit()  # type: ignore[attr-defined]

    def rollback(self) -> None:
        assert self._entered is not None
        self._entered.rollback()  # type: ignore[attr-defined]

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._delegate.__exit__(exc_type, exc, traceback)  # type: ignore[attr-defined]

class FollowerFinalizeAfterOwnerMarkMediaPort:
    def __init__(self, delegate: MediaTransactionPort, race: MarkReferencedRace) -> None:
        self._delegate = delegate
        self._race = race

    def reserve(self, request: MediaReservationRequest) -> MediaLease:
        return self._delegate.reserve(request)

    def find_idempotent_outcome(
        self,
        owner_id: str,
        session_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> FinalizeMediaOutcome | None:
        return self._delegate.find_idempotent_outcome(
            owner_id,
            session_id,
            idempotency_key,
            fingerprint,
        )

    def finalize(self, request: FinalizeMediaRequest) -> FinalizeMediaOutcome:
        self._race.follower_finalize_waiting.set()
        if not self._race.owner_mark_ready.wait(timeout=10):
            raise AssertionError("owner did not publish pending reference intent")
        return self._delegate.finalize(request)

    def confirm_reference(self, intent: MediaReferenceIntent) -> IngestedEvidenceResult:
        return self._delegate.confirm_reference(intent)

    def list_pending_reference_outcomes(
        self,
        limit: int,
    ) -> tuple[FinalizeMediaOutcome, ...]:
        return self._delegate.list_pending_reference_outcomes(limit)

    def reject(self, lease: MediaLease, reason: str) -> None:
        self._delegate.reject(lease, reason)


class FollowerFinalizeAfterOwnerMarkUowFactory:
    def __init__(self, delegate: UnitOfWorkFactory, race: MarkReferencedRace) -> None:
        self._delegate = delegate
        self._race = race

    def __call__(self) -> object:
        return FollowerFinalizeAfterOwnerMarkUow(self._delegate(), self._race)


class FollowerFinalizeAfterOwnerMarkUow:
    def __init__(self, delegate: object, race: MarkReferencedRace) -> None:
        self._delegate = delegate
        self._race = race
        self._entered: object | None = None
        self._media: MediaTransactionPort | None = None

    def __enter__(self) -> object:
        self._entered = self._delegate.__enter__()  # type: ignore[attr-defined]
        self._media = FollowerFinalizeAfterOwnerMarkMediaPort(
            self._entered.media,  # type: ignore[attr-defined]
            self._race,
        )
        return self

    @property
    def media(self) -> MediaTransactionPort:
        assert self._media is not None
        return self._media

    @property
    def scan_audit(self) -> object:
        assert self._entered is not None
        return self._entered.scan_audit  # type: ignore[attr-defined]

    def commit(self) -> None:
        assert self._entered is not None
        self._entered.commit()  # type: ignore[attr-defined]

    def rollback(self) -> None:
        assert self._entered is not None
        self._entered.rollback()  # type: ignore[attr-defined]

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._delegate.__exit__(exc_type, exc, traceback)  # type: ignore[attr-defined]


def request() -> MediaReservationRequest:
    return MediaReservationRequest(
        owner_id="owner-1",
        session_id="session-1",
        idempotency_key="idem-1",
        fingerprint="fingerprint-1",
    )


def real_uow_factory(database_path: Path) -> tuple[UnitOfWorkFactory, object]:
    engine = create_database_engine(f"sqlite+pysqlite:///{database_path}")
    Base.metadata.create_all(engine)
    return UnitOfWorkFactory(create_session_factory(engine)), engine


def create_real_learning_session(factory: UnitOfWorkFactory) -> None:
    now = datetime.now(UTC)
    with factory() as uow:
        uow.sessions.create(
            StoredSession(
                session_id="session-1",
                owner_user_id="owner-1",
                status="running",
                adapter_mode="openhands-local-scripted-test",
                domain="general",
                title="Replay",
                goal="Explain replay",
                expected_output=None,
                planned_minutes=20,
                conversation_id=str(uuid5(NAMESPACE_URL, "focusproof:session-1")),
                runtime_mode="openhands-local-scripted-test",
                review_result=None,
                goal_conversation_synced_at=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        uow.commit()


class ActiveCommitAfterFaultFactory:
    def __init__(self, delegate: UnitOfWorkFactory) -> None:
        self._delegate = delegate
        self.fired = False

    def __call__(self) -> object:
        return ActiveCommitAfterFaultUow(self)


class ActiveCommitAfterFaultUow:
    def __init__(self, factory: ActiveCommitAfterFaultFactory) -> None:
        self._factory = factory
        self._delegate = factory._delegate()
        self._entered: object | None = None

    def __enter__(self) -> object:
        self._entered = self._delegate.__enter__()
        return self

    @property
    def media(self) -> object:
        assert self._entered is not None
        return self._entered.media  # type: ignore[attr-defined]

    @property
    def scan_audit(self) -> object:
        assert self._entered is not None
        return self._entered.scan_audit  # type: ignore[attr-defined]

    def commit(self) -> None:
        assert self._entered is not None
        session = self._entered._require_session()  # type: ignore[attr-defined]
        self._entered.commit()  # type: ignore[attr-defined]
        if (
            not self._factory.fired
            and session.scalar(select(func.count()).select_from(MediaCleanReceiptModel)) == 1
        ):
            self._factory.fired = True
            raise RuntimeError("after active receipt commit fault")

    def rollback(self) -> None:
        assert self._entered is not None
        self._entered.rollback()  # type: ignore[attr-defined]

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._delegate.__exit__(exc_type, exc, traceback)  # type: ignore[arg-type]


def make_real_service(
    recorder: Recorder,
    *,
    factory: object,
    quarantine: LocalQuarantineStore,
    cleanup_errors: list[CleanupError] | None = None,
) -> MediaIngestionService:
    reporter = cleanup_errors.append if cleanup_errors is not None else (lambda error: None)
    return MediaIngestionService(
        uow_factory=factory,  # type: ignore[arg-type]
        quarantine_store=quarantine,
        malware_scanner=FakeMalwareScanner(recorder),
        validator=FakeValidator(recorder),
        normalizer=FakeNormalizer(recorder, b"normalized"),
        object_store=FakeObjectStore(recorder),
        cleanup_error_reporter=reporter,
    )


def make_full_real_service(
    recorder: Recorder,
    *,
    factory: object,
    quarantine: LocalQuarantineStore,
    codec: object,
    object_store: LocalMediaObjectStore,
) -> MediaIngestionService:
    return MediaIngestionService(
        uow_factory=factory,  # type: ignore[arg-type]
        quarantine_store=quarantine,
        malware_scanner=FakeMalwareScanner(recorder),
        validator=codec,  # type: ignore[arg-type]
        normalizer=codec,  # type: ignore[arg-type]
        object_store=object_store,
        cleanup_error_reporter=lambda error: None,
    )


def seed_published_clean_capability(
    factory: UnitOfWorkFactory,
    quarantine: LocalQuarantineStore,
    payload: bytes,
) -> PendingCleanReceipt:
    now = datetime.now(UTC)
    digest = sha256(payload).hexdigest()
    writer = quarantine.create_untrusted_scan_spool("published-replay-seed")
    writer.write(payload)
    spool = writer.finalize()
    writer.close()
    attempt = MediaScanAttempt(
        attempt_id="attempt-published-replay",
        artifact_sha256=digest,
        content_type=PNG_MEDIA_TYPE,
        scanner_backend="seeded-clean-scanner",
        definitions_version="seeded-definitions-v1",
        definitions_fresh_at=now,
        definitions_age_seconds=0,
        max_bytes=10 * MiB,
        max_concurrent_scans=1,
        deadline_ms=5000,
        socket_timeout_ms=2000,
        scan_result=ScanResultKind.CLEAN,
        rejection_code=None,
        rejection_detail=None,
        started_at=now,
        finished_at=now,
        idempotency_key="session-1:idem-1:fingerprint-1",
    )
    pending = quarantine.pending_clean_receipt(
        spool,
        receipt_id="receipt-published-replay",
        attempt_id=attempt.attempt_id,
        artifact_sha256=digest,
        receipt_hash="b" * 64,
        created_at=now,
    )
    with factory() as uow:
        attempt = uow.scan_audit.record_attempt(attempt)
        pending = uow.scan_audit.record_pending_clean_receipt(pending)
        claim = uow.scan_audit.claim_pending_clean_publication(
            pending.receipt_id,
            owner_token="published-replay-seed-owner",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        assert claim is not None
        assert claim.acquired is True
        pending = claim.pending
        uow.commit()
    formal = quarantine.promote_clean_spool(
        spool,
        receipt_id=pending.receipt_id,
        receipt_hash=pending.receipt_hash,
        formal_artifact_id=pending.formal_artifact_id,
        quarantine_expires_at=pending.quarantine_expires_at,
    )
    formal.close()
    published_at = datetime.now(UTC)
    with factory() as uow:
        pending = uow.scan_audit.mark_pending_clean_publication_published(
            pending.receipt_id,
            owner_token="published-replay-seed-owner",
            formal_artifact_id=pending.formal_artifact_id,
            now=published_at,
        )
        uow.scan_audit.record_clean_receipt(
            MediaCleanReceipt.from_attempt(
                attempt,
                receipt_id=pending.receipt_id,
                receipt_hash=pending.receipt_hash,
                quarantine_path=pending.formal_artifact_id,
                quarantine_expires_at=pending.quarantine_expires_at,
                created_at=pending.created_at,
            )
        )
        uow.commit()
    with factory() as uow:
        uow.media.reserve(request())
        uow.commit()
    return pending


def assert_real_convergence(
    *,
    factory: UnitOfWorkFactory,
    quarantine_root: Path,
    object_root: Path,
    payload: bytes,
    results: list[IngestedEvidenceResult],
) -> None:
    assert len(results) == 2
    assert len({result.media_item_id for result in results}) == 1
    assert len({result.artifact_ref for result in results}) == 1
    with factory() as uow:
        session = uow._require_session()
        assert session.scalar(select(func.count()).select_from(MediaScanAttemptModel)) == 1
        assert session.scalar(select(func.count()).select_from(PendingCleanReceiptModel)) == 1
        assert session.scalar(select(func.count()).select_from(MediaCleanReceiptModel)) == 1
        assert session.scalar(select(func.count()).select_from(MediaArtifactModel)) == 1
        assert session.scalar(select(func.count()).select_from(EvidenceModel)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEventModel)) == 0
        pending = session.scalar(select(PendingCleanReceiptModel))
        active = session.scalar(select(MediaCleanReceiptModel))
        artifact = session.scalar(select(MediaArtifactModel))
        assert pending is not None
        assert pending.publication_status == "published"
        assert active is not None
        assert artifact is not None
        opaque_object_key = artifact.opaque_object_key
        uow.commit()
    quarantine = LocalQuarantineStore(quarantine_root)
    formal = quarantine.find_promoted_clean_receipt(
        receipt_id=active.receipt_id,
        receipt_hash=active.receipt_hash,
        artifact_sha256=active.artifact_sha256,
    )
    assert formal is not None
    with formal.open() as stream:
        assert stream.read() == payload
    with LocalMediaObjectStore(object_root).open(opaque_object_key) as stream:
        assert stream.read()
    assert len(list((quarantine_root / "payloads").glob("[!.]*"))) == 1
    assert len(list((quarantine_root / "records").glob("*.json"))) == 1
    assert len(list((quarantine_root / "commits").glob("*.commit"))) == 1
    assert list((quarantine_root / "untrusted-scan-spool").iterdir()) == []
    assert list((object_root / "staged").iterdir()) == []
    assert list((object_root / "manifests").iterdir()) == []
    assert len(list((object_root / "referenced").iterdir())) == 1


def pending_clean_replay(payload: bytes = b"opaque source") -> tuple[MediaScanAttempt, PendingCleanReceipt]:
    snapshot = FakeScanSnapshot()
    started_at = datetime.now(UTC)
    finished_at = started_at + timedelta(seconds=1)
    stable_key = "session-1:idem-1:fingerprint-1"
    artifact_sha256 = sha256(payload).hexdigest()
    attempt = MediaScanAttempt(
        attempt_id="attempt-replay",
        artifact_sha256=artifact_sha256,
        content_type=MEDIA_TYPE,
        scanner_backend=snapshot.scanner_backend,
        definitions_version=snapshot.definitions_version,
        definitions_fresh_at=snapshot.definitions_fresh_at,
        definitions_age_seconds=snapshot.definitions_age_seconds,
        max_bytes=snapshot.max_bytes,
        max_concurrent_scans=snapshot.max_concurrent_scans,
        deadline_ms=snapshot.deadline_ms,
        socket_timeout_ms=snapshot.socket_timeout_ms,
        scan_result=ScanResultKind.CLEAN,
        rejection_code=None,
        rejection_detail=None,
        started_at=started_at,
        finished_at=finished_at,
        idempotency_key=stable_key,
    )
    pending = PendingCleanReceipt(
        receipt_id="receipt-replay",
        attempt_id=attempt.attempt_id,
        artifact_sha256=artifact_sha256,
        receipt_hash="e" * 64,
        spool_token="spool-replay",
        spool_byte_size=len(payload),
        spool_sha256=artifact_sha256,
        spool_expires_at=finished_at + timedelta(minutes=1),
        quarantine_expires_at=finished_at + timedelta(hours=1),
        created_at=finished_at,
    )
    return attempt, pending


def replay_outcome(action: ReferenceAction, *, evidence_visible: bool | None = None) -> FinalizeMediaOutcome:
    media_item_id = "media-item-1" if action == "MARK_REFERENCED" else "media-item-existing"
    visible = evidence_visible if evidence_visible is not None else action != "MARK_REFERENCED"
    return FinalizeMediaOutcome(
        result=IngestedEvidenceResult(
            evidence_id="evidence-old",
            media_item_id=media_item_id,
            artifact_ref=f"focusproof-artifact://{media_item_id}",
            media_type=MEDIA_TYPE,
            normalized_sha256="hash",
            byte_size=1,
            learner_explanation="already committed",
            attributes={"codec_note": "replayed"},
        ),
        reference_intent=MediaReferenceIntent(
            staged=StagedMediaObject(
                media_item_id="media-item-1",
                reservation_id="reservation-1",
                opaque_object_key="opaque-key",
                manifest_id="manifest-1",
            ),
            action=action,
        ),
        evidence_visible=visible,
    )


def replay_abort_self_outcome(*, evidence_visible: bool) -> FinalizeMediaOutcome:
    return FinalizeMediaOutcome(
        result=IngestedEvidenceResult(
            evidence_id="evidence-old",
            media_item_id="media-item-1",
            artifact_ref="focusproof-artifact://media-item-1",
            media_type=MEDIA_TYPE,
            normalized_sha256="hash",
            byte_size=1,
            learner_explanation="bad replay",
            attributes={},
        ),
        reference_intent=MediaReferenceIntent(
            staged=StagedMediaObject(
                media_item_id="media-item-1",
                reservation_id="reservation-1",
                opaque_object_key="opaque-key",
                manifest_id="manifest-1",
            ),
            action="ABORT_STAGED",
        ),
        evidence_visible=evidence_visible,
    )


def test_pending_replay_marks_confirms_and_returns_confirmed_result_before_new_reservation() -> None:
    recorder = Recorder()
    replay = replay_outcome("MARK_REFERENCED")
    service, media, _ = make_service(recorder, replay=replay)

    result = service.ingest(
        request=request(),
        chunks=[b"not-read"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="ignored",
    )

    assert result == replay.result
    assert media.confirm_intents == [replay.reference_intent]
    assert recorder.events == [
        "uow.enter",
        "tx.replay:owner-1:session-1:idem-1:fingerprint-1",
        "uow.commit",
        "uow.exit",
        "object.mark_referenced:media-item-1",
        "uow.enter",
        "tx.confirm:media-item-1:MARK_REFERENCED",
        "uow.commit",
        "uow.exit",
    ]


def test_completed_reuse_replay_aborts_staged_intent_without_reservation() -> None:
    recorder = Recorder()
    replay = replay_outcome("ABORT_STAGED")
    service, media, _ = make_service(recorder, replay=replay)

    result = service.ingest(
        request=request(),
        chunks=[b"not-read"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="ignored",
    )

    assert result == replay.result
    assert media.confirm_intents == []
    assert media.reject_reasons == []
    assert "object.abort_staged:media-item-1" in recorder.events
    assert "object.mark_referenced:media-item-1" not in recorder.events


def test_pending_follower_replay_aborts_staged_intent_then_raises_retryable_pending() -> None:
    recorder = Recorder()
    replay = replay_outcome("ABORT_STAGED", evidence_visible=False)
    service, media, _ = make_service(recorder, replay=replay)

    with pytest.raises(MediaReferencePendingError, match="reference is pending") as exc_info:
        service.ingest(
            request=request(),
            chunks=[b"not-read"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="ignored",
        )

    assert media.confirm_intents == []
    assert media.reject_reasons == []
    assert "object.abort_staged:media-item-1" in recorder.events
    assert exc_info.value.retryable is True
    assert "opaque-key" not in str(exc_info.value)


def test_noop_replay_returns_result_without_store_or_confirm() -> None:
    recorder = Recorder()
    replay = replay_outcome("NOOP")
    service, media, _ = make_service(recorder, replay=replay)

    result = service.ingest(
        request=request(),
        chunks=[b"not-read"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="ignored",
    )

    assert result == replay.result
    assert media.confirm_intents == []
    assert not any(event.startswith("object.") for event in recorder.events)


def test_reservation_happens_before_create_and_stream_processing() -> None:
    recorder = Recorder()
    service, _, _ = make_service(recorder, committed_count=4)

    with pytest.raises(MediaQuotaExceeded, match="media item count quota exceeded"):
        service.ingest(
            request=request(),
            chunks=[b"must-not-stream"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="count full",
        )

    assert "quarantine.create:reservation-1" not in recorder.events
    assert "writer.write:15" not in recorder.events
    assert "tx.reserve:owner-1:session-1" in recorder.events


def test_create_failure_after_lease_rejects_without_writer_cleanup() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, fail_create=True)

    with pytest.raises(RuntimeError, match="create failed"):
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="create fail",
        )

    assert media.reject_reasons == ["RuntimeError"]
    assert "writer.abort" not in recorder.events
    assert "writer.close" not in recorder.events


def test_new_adopted_result_marks_confirms_and_verifies_normalized_facts() -> None:
    recorder = Recorder()
    attrs: Mapping[str, JSONScalar] = {"codec_note": "transparent", "ordinal": 9, "blank": None}
    service, media, object_store = make_service(recorder, payload=b"normalized", attributes=attrs)

    result = service.ingest(
        request=request(),
        chunks=[b"abc", b"def"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="I created this binary fixture.",
    )

    expected_hash = sha256(b"normalized").hexdigest()
    final_request = media.final_requests[0]
    assert result.normalized_sha256 == expected_hash
    assert result.byte_size == len(b"normalized")
    assert final_request.normalized_sha256 == expected_hash
    assert final_request.normalized_byte_size == len(b"normalized")
    assert dict(final_request.attributes) == attrs
    assert dict(result.attributes) == attrs
    assert object_store.stage_positions == [0]
    assert recorder.events.index("normalized.rewind") + 1 == recorder.events.index(
        "object.stage:media-item-1:reservation-1"
    )
    assert recorder.events.count("writer.close") == 1
    assert "object.mark_referenced:media-item-1" in recorder.events
    assert media.confirm_intents == [
        MediaReferenceIntent(
            staged=StagedMediaObject(
                media_item_id="media-item-1",
                reservation_id="reservation-1",
                opaque_object_key="opaque-key",
                manifest_id="manifest-1",
            ),
            action="MARK_REFERENCED",
        )
    ]
    assert media.reject_reasons == []
    with pytest.raises(TypeError):
        final_request.attributes["new"] = "blocked"  # type: ignore[index]


def test_source_limit_aborts_writer_before_finalize_and_closes_once() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder)

    with pytest.raises(MediaQuotaExceeded, match="media source exceeds"):
        service.ingest(
            request=request(),
            chunks=[b"x" * (10 * MiB), b"x"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="too large",
        )

    assert media.reject_reasons == ["MediaQuotaExceeded"]
    assert "writer.abort" in recorder.events
    assert "writer.finalize" not in recorder.events
    assert recorder.events.count("writer.close") == 1
    assert "quarantine.delete" not in recorder.events


def test_normalized_exact_message_limit_is_accepted() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, payload=b"x" * (10 * MiB))

    result = service.ingest(
        request=request(),
        chunks=[b"small source"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="exact normalized boundary",
    )

    assert result.byte_size == 10 * MiB
    assert len(media.final_requests) == 1
    assert "object.stage:media-item-1:reservation-1" in recorder.events


def test_normalized_one_byte_over_message_limit_rejects_before_stage_and_finalize() -> None:
    recorder = Recorder()
    service, media, _ = make_service(
        recorder,
        payload=b"x" * (10 * MiB + 1),
    )

    with pytest.raises(MediaQuotaExceeded, match="canonical media message exceeds"):
        service.ingest(
            request=request(),
            chunks=[b"small source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="oversized normalized output",
        )

    assert media.reject_reasons == ["MediaQuotaExceeded"]
    assert media.final_requests == []
    assert not any(event.startswith("object.stage:") for event in recorder.events)
    assert "normalized.close" in recorder.events
    assert "quarantine.delete" not in recorder.events
    assert "quarantine.close" in recorder.events
    assert "quarantine.close" in recorder.events


def test_quarantine_metadata_spoof_rejects_and_deletes_quarantine() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, spoof_quarantine=True)

    with pytest.raises(QuarantineMetadataMismatchError, match="quarantine metadata"):
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="spoofed",
        )

    assert media.reject_reasons == ["QuarantineMetadataMismatchError"]
    assert "spool.delete" in recorder.events
    assert "spool.close" in recorder.events
    assert "quarantine.delete" not in recorder.events
    assert recorder.events.count("writer.close") == 1


def test_normalized_metadata_mismatch_rejects_and_closes_sources() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, bad_normalized_metadata=True)

    with pytest.raises(ValueError, match="normalized media source metadata mismatch"):
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="bad normalized source",
        )

    assert media.reject_reasons == ["ValueError"]
    assert "normalized.close" in recorder.events
    assert recorder.events[-2:] == ["normalized.close", "quarantine.close"]


def test_validated_metadata_mismatch_rejects_before_normalize() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, spoof_validated_metadata=True)

    with pytest.raises(ValidatedMetadataMismatchError, match="validated metadata"):
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="bad validator",
        )

    assert media.reject_reasons == ["ValidatedMetadataMismatchError"]
    assert not any(event.startswith("normalizer.normalize") for event in recorder.events)
    assert "quarantine.delete" not in recorder.events
    assert "quarantine.close" in recorder.events


def test_cleanup_failures_do_not_cover_primary_failure() -> None:
    recorder = Recorder()
    cleanup_errors: list[CleanupError] = []
    service, media, _ = make_service(
        recorder,
        committed_distinct_bytes=20 * MiB,
        payload=b"x",
        fail_abort_staged=True,
        fail_normalized_close=True,
        fail_reject=True,
        cleanup_errors=cleanup_errors,
    )

    with pytest.raises(MediaQuotaExceeded, match="normalized distinct byte quota exceeded") as exc_info:
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="quota loser",
        )

    assert media.reject_reasons == ["MediaQuotaExceeded"]
    assert "object.abort_staged:media-item-1" in recorder.events
    assert "normalized.close" in recorder.events
    assert "quarantine.delete" not in recorder.events
    assert "quarantine.close" in recorder.events
    assert len(cleanup_errors) == 1
    assert len(cleanup_errors[0].errors) == 3
    assert "media cleanup failed: RuntimeError" in exc_info.value.__notes__


def test_writer_abort_and_close_failures_do_not_cover_source_limit_failure() -> None:
    recorder = Recorder()
    cleanup_errors: list[CleanupError] = []
    service, media, _ = make_service(
        recorder,
        fail_abort=True,
        fail_writer_close=True,
        cleanup_errors=cleanup_errors,
    )

    with pytest.raises(MediaQuotaExceeded, match="media source exceeds") as exc_info:
        service.ingest(
            request=request(),
            chunks=[b"x" * (10 * MiB), b"x"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="too large",
        )

    assert media.reject_reasons == ["MediaQuotaExceeded"]
    assert recorder.events.count("writer.abort") == 1
    assert recorder.events.count("writer.close") == 1
    assert len(cleanup_errors) == 1
    assert len(cleanup_errors[0].errors) == 2
    assert "media cleanup failed: RuntimeError" in exc_info.value.__notes__


def test_success_path_cleanup_failure_is_reported_without_changing_result() -> None:
    recorder = Recorder()
    cleanup_errors: list[CleanupError] = []
    service, _, _ = make_service(
        recorder,
        fail_writer_close=True,
        cleanup_errors=cleanup_errors,
    )

    result = service.ingest(
        request=request(),
        chunks=[b"abc"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="cleanup visible",
    )

    assert result.evidence_id == "evidence-1"
    assert len(cleanup_errors) == 1
    assert len(cleanup_errors[0].errors) == 1


def test_reused_result_aborts_staged_intent_after_commit_without_mark_confirm_or_reject() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, reference_action="ABORT_STAGED")

    result = service.ingest(
        request=request(),
        chunks=[b"abc"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="reused",
    )

    assert result.media_item_id == "media-item-existing"
    assert len(media.final_requests) == 1
    assert media.confirm_intents == []
    assert media.reject_reasons == []
    assert "object.abort_staged:media-item-1" in recorder.events
    assert "object.mark_referenced:media-item-1" not in recorder.events


def test_reused_abort_failure_is_reported_and_committed_result_is_returned() -> None:
    recorder = Recorder()
    cleanup_errors: list[CleanupError] = []
    service, media, _ = make_service(
        recorder,
        reference_action="ABORT_STAGED",
        fail_abort_staged=True,
        cleanup_errors=cleanup_errors,
    )

    result = service.ingest(
        request=request(),
        chunks=[b"abc"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="reused cleanup",
    )

    assert result.media_item_id == "media-item-existing"
    assert media.confirm_intents == []
    assert media.reject_reasons == []
    assert "object.abort_staged:media-item-1" in recorder.events
    assert "object.mark_referenced:media-item-1" not in recorder.events
    assert len(cleanup_errors) == 1
    assert len(cleanup_errors[0].errors) == 1


def test_pending_follower_aborts_staged_then_raises_retryable_pending_without_reject() -> None:
    recorder = Recorder()
    service, media, _ = make_service(
        recorder,
        reference_action="ABORT_STAGED",
        evidence_visible=False,
    )

    with pytest.raises(MediaReferencePendingError, match="reference is pending") as exc_info:
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="pending follower",
        )

    assert media.reject_reasons == []
    assert "object.abort_staged:media-item-1" in recorder.events
    assert recorder.events.count("object.abort_staged:media-item-1") == 1
    assert "object.mark_referenced:media-item-1" not in recorder.events
    assert exc_info.value.retryable is True
    assert "opaque-key" not in str(exc_info.value)


def assert_fresh_mark_wrong_tuple_fails_closed(
    *,
    intent_media_item_id: str | None = None,
    intent_reservation_id: str | None = None,
    intent_opaque_object_key: str | None = None,
    intent_manifest_id: str | None = None,
) -> None:
    recorder = Recorder()
    service, media, _ = make_service(
        recorder,
        intent_media_item_id=intent_media_item_id,
        intent_reservation_id=intent_reservation_id,
        intent_opaque_object_key=intent_opaque_object_key,
        intent_manifest_id=intent_manifest_id,
    )

    with pytest.raises(InvalidMediaReferenceOutcomeError, match="media reference outcome"):
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="bad staged tuple",
        )

    assert media.reject_reasons == []
    assert not any(event.startswith("object.mark") for event in recorder.events)


def test_fresh_mark_with_wrong_staged_tuple_fails_closed_without_mark() -> None:
    assert_fresh_mark_wrong_tuple_fails_closed(intent_media_item_id="media-item-other")
    assert_fresh_mark_wrong_tuple_fails_closed(intent_reservation_id="reservation-other")
    assert_fresh_mark_wrong_tuple_fails_closed(intent_opaque_object_key="opaque-other")
    assert_fresh_mark_wrong_tuple_fails_closed(intent_manifest_id="manifest-other")


def test_fresh_abort_with_winner_staged_tuple_deletes_only_caller_stage() -> None:
    recorder = Recorder()
    service, media, _ = make_service(
        recorder,
        reference_action="ABORT_STAGED",
        intent_opaque_object_key="opaque-other",
    )

    result = service.ingest(
        request=request(),
        chunks=[b"abc"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="winner staged tuple",
    )

    assert result.media_item_id == "media-item-existing"
    assert media.reject_reasons == []
    assert "object.abort_staged:media-item-1" in recorder.events
    assert "object.abort_staged:media-item-other" not in recorder.events


def test_fresh_noop_cleans_caller_staged_and_returns_completed_winner() -> None:
    recorder = Recorder()
    service, media, _ = make_service(
        recorder,
        reference_action="NOOP",
        result_media_item_id="media-item-1",
    )

    result = service.ingest(
        request=request(),
        chunks=[b"abc"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="concurrent noop loser",
    )

    assert result.media_item_id == "media-item-1"
    assert media.reject_reasons == []
    assert not any(event.startswith("object.mark") for event in recorder.events)
    assert "object.abort_staged:media-item-1" in recorder.events


def test_abort_visible_result_pointing_to_stable_media_id_cleans_caller_stage() -> None:
    recorder = Recorder()
    service, media, _ = make_service(
        recorder,
        reference_action="ABORT_STAGED",
        result_media_item_id="media-item-1",
    )

    result = service.ingest(
        request=request(),
        chunks=[b"abc"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="concurrent abort loser",
    )

    assert result.media_item_id == "media-item-1"
    assert media.reject_reasons == []
    assert "object.abort_staged:media-item-1" in recorder.events


def test_abort_pending_result_pointing_to_stable_id_reconciles_after_caller_cleanup() -> None:
    recorder = Recorder()
    service, media, _ = make_service(
        recorder,
        reference_action="ABORT_STAGED",
        evidence_visible=False,
        result_media_item_id="media-item-1",
    )

    service._reconcile_pending_reference = (  # type: ignore[method-assign]
        lambda lease, cleanup_errors: replay_outcome("NOOP").result
    )
    result = service.ingest(
        request=request(),
        chunks=[b"abc"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="pending concurrent loser",
    )

    assert result.media_item_id == "media-item-existing"
    assert media.reject_reasons == []
    assert "object.abort_staged:media-item-1" in recorder.events


@pytest.mark.parametrize("evidence_visible", [True, False])
def test_replay_abort_result_pointing_to_staged_self_fails_closed_without_abort(
    evidence_visible: bool,
) -> None:
    recorder = Recorder()
    replay = replay_abort_self_outcome(evidence_visible=evidence_visible)
    service, media, _ = make_service(recorder, replay=replay)

    with pytest.raises(InvalidMediaReferenceOutcomeError, match="media reference outcome"):
        service.ingest(
            request=request(),
            chunks=[b"not-read"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="ignored",
        )

    assert media.reject_reasons == []
    assert "object.abort_staged:media-item-1" not in recorder.events


def test_confirm_failure_after_mark_needs_reconciliation_without_reject_or_abort() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, fail_confirm=True)

    with pytest.raises(PostCommitReferenceError, match="reconciliation"):
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="committed",
        )

    assert len(media.final_requests) == 1
    assert len(media.confirm_intents) == 1
    assert media.reject_reasons == []
    assert "object.mark_referenced:media-item-1" in recorder.events
    assert "object.abort_staged:media-item-1" not in recorder.events


def test_post_commit_mark_failure_needs_reconciliation_without_reject_or_abort() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, fail_mark_referenced=True)

    with pytest.raises(PostCommitReferenceError, match="reconciliation"):
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="committed",
        )

    assert len(media.final_requests) == 1
    assert media.reject_reasons == []
    assert "object.abort_staged:media-item-1" not in recorder.events
    assert "object.mark_referenced:media-item-1" in recorder.events
    assert media.confirm_intents == []


def test_completed_replay_with_different_manifest_does_not_mask_mark_failure() -> None:
    recorder = Recorder()
    stale = replay_outcome("MARK_REFERENCED")
    mismatched_staged = replace(
        stale.reference_intent.staged,
        manifest_id="manifest-other",
    )
    completed_mismatch = replace(
        stale,
        reference_intent=MediaReferenceIntent(
            staged=mismatched_staged,
            action="NOOP",
        ),
        evidence_visible=True,
    )
    service, media, object_store = make_service(recorder, replay=stale)

    def fail_and_publish_mismatch(staged: StagedMediaObject) -> None:
        recorder.add(f"object.mark_referenced:{staged.media_item_id}")
        media.replay = completed_mismatch
        raise ValueError("staged manifest binding mismatch")

    object_store.mark_referenced = fail_and_publish_mismatch  # type: ignore[method-assign]

    with pytest.raises(PostCommitReferenceError, match="reconciliation"):
        service.ingest(
            request=request(),
            chunks=[b"not-read"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="mismatched replay",
        )

    assert media.confirm_intents == []
    assert media.reject_reasons == []


@pytest.mark.parametrize(
    ("action", "evidence_visible", "result_media_item_id"),
    [
        ("MARK_REFERENCED", True, "media-item-1"),
        ("MARK_REFERENCED", False, "media-item-other"),
        ("NOOP", False, "media-item-existing"),
    ],
)
def test_contradictory_outcome_fails_closed_after_finalize_without_reject(
    action: ReferenceAction,
    evidence_visible: bool,
    result_media_item_id: str,
) -> None:
    recorder = Recorder()
    service, media, _ = make_service(
        recorder,
        reference_action=action,
        evidence_visible=evidence_visible,
        result_media_item_id=result_media_item_id,
    )

    with pytest.raises(ValueError, match="media reference outcome"):
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="bad outcome",
        )

    assert len(media.final_requests) == 1
    assert media.reject_reasons == []


def test_transaction_port_exposes_bounded_pending_reference_outcomes() -> None:
    recorder = Recorder()
    replay = replay_outcome("MARK_REFERENCED")
    _, media, _ = make_service(recorder, replay=replay)

    assert media.list_pending_reference_outcomes(1) == (replay,)
    assert media.list_pending_reference_outcomes(0) == ()


def test_final_distinct_quota_decision_is_delegated_and_failure_aborts_staged_object() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, committed_distinct_bytes=20 * MiB, payload=b"x")

    with pytest.raises(MediaQuotaExceeded, match="normalized distinct byte quota exceeded"):
        service.ingest(
            request=request(),
            chunks=[b"abc"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="quota loser",
        )

    assert media.reject_reasons == ["MediaQuotaExceeded"]
    assert "object.abort_staged:media-item-1" in recorder.events
    assert "object.mark_referenced:media-item-1" not in recorder.events


@pytest.mark.parametrize(
    ("status", "error_type", "reason"),
    [
        ("malicious", MalwareDetectedError, "media_malware_detected"),
        ("oversize", MalwareScanFailedError, "media_scan_failed"),
        ("unavailable", MalwareScanUnavailableError, "media_scan_unavailable"),
        ("timeout", MalwareScanTimeoutError, "media_scan_timeout"),
        ("error", MalwareScanFailedError, "media_scan_failed"),
        ("unknown", MalwareScanFailedError, "media_scan_failed"),
    ],
)
def test_non_clean_scan_fails_before_validation_and_persistence(
    status: str, error_type: type[Exception], reason: str
) -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, scanner_status=status)

    with pytest.raises(error_type):
        service.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
        )

    assert not any(
        event.startswith(("validator.", "normalizer.", "object.stage", "tx.finalize", "object.mark", "tx.confirm"))
        for event in recorder.events
    )
    assert media.reject_reasons == [reason]
    assert len(recorder.attempts) == 1
    assert recorder.receipts == []
    attempt_index = next(
        index for index, event in enumerate(recorder.events)
        if event.startswith("scan_attempt.record:")
    )
    reject_index = next(
        index for index, event in enumerate(recorder.events)
        if event.startswith("tx.reject:")
    )
    commit_index = next(
        index for index, event in enumerate(recorder.events[reject_index:], reject_index)
        if event == "uow.commit"
    )
    assert attempt_index < reject_index < commit_index
    assert "uow.commit" not in recorder.events[attempt_index:reject_index]
    assert sum(event.startswith("tx.reject:") for event in recorder.events) == 1
    assert recorder.events.index("writer.finalize") < next(
        i for i, event in enumerate(recorder.events) if event.startswith("scanner.scan:")
    )
    assert "spool.stream.close" in recorder.events
    assert "spool.delete" in recorder.events
    assert "spool.close" in recorder.events
    assert "quarantine.delete" not in recorder.events


def test_non_clean_default_rejections_are_resolved_by_neutral_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from focusproof.media_core import ingestion as ingestion_module

    calls: list[ScanResultKind] = []

    def recording_resolver(result: ScanResultKind):
        calls.append(result)
        return default_scan_rejection_code(result)

    monkeypatch.setattr(
        ingestion_module,
        "default_scan_rejection_code",
        recording_resolver,
    )

    expected_results = [
        ("malicious", ScanResultKind.MALICIOUS),
        ("oversize", ScanResultKind.OVERSIZE),
        ("unavailable", ScanResultKind.UNAVAILABLE),
        ("timeout", ScanResultKind.TIMEOUT),
        ("error", ScanResultKind.ERROR),
        ("unknown", ScanResultKind.ERROR),
    ]
    for status, expected_result in expected_results:
        recorder = Recorder()
        service, _, _ = make_service(recorder, scanner_status=status)

        with pytest.raises(
            (
                MalwareDetectedError,
                MalwareScanFailedError,
                MalwareScanTimeoutError,
                MalwareScanUnavailableError,
            )
        ):
            service.ingest(
                request=request(),
                chunks=[b"opaque source"],
                declared_media_type=MEDIA_TYPE,
                learner_explanation="evidence",
            )

        assert len(recorder.attempts) == 1
        assert recorder.receipts == []
        assert recorder.attempts[0].scan_result == expected_result
        assert recorder.attempts[0].rejection_code == default_scan_rejection_code(
            expected_result
        )

    assert calls == [result for _, result in expected_results]


def test_clean_scan_runs_after_integrity_check_and_before_validator() -> None:
    recorder = Recorder()
    service, _, _ = make_service(recorder)

    service.ingest(
        request=request(),
        chunks=[b"opaque source"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="evidence",
    )

    scanner_index = next(
        i for i, event in enumerate(recorder.events) if event.startswith("scanner.scan:")
    )
    validator_index = next(
        i for i, event in enumerate(recorder.events) if event.startswith("validator.validate:")
    )
    assert recorder.events.index("writer.finalize") < scanner_index < validator_index


def test_clean_scan_requires_atomic_attempt_and_receipt_before_media_completion() -> None:
    """Missing scan-audit participation in the ingestion UoW is a correctness bug."""
    recorder = Recorder()
    service, _, _ = make_service(recorder)

    service.ingest(
        request=request(),
        chunks=[b"opaque source"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="evidence",
    )

    attempt_index = next(
        index for index, event in enumerate(recorder.events)
        if event.startswith("scan_attempt.record:")
    )
    receipt_index = next(
        index for index, event in enumerate(recorder.events)
        if event.startswith("clean_receipt.record:")
    )
    finalize_index = next(
        index for index, event in enumerate(recorder.events)
        if event.startswith("tx.finalize:")
    )
    assert attempt_index < receipt_index < finalize_index


def test_clean_scan_uses_untrusted_spool_then_promotes_after_pending_receipt() -> None:
    recorder = Recorder()
    service, _, _ = make_service(recorder)

    service.ingest(
        request=request(),
        chunks=[b"opaque source"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="evidence",
    )

    scanner_index = next(
        index for index, event in enumerate(recorder.events)
        if event.startswith("scanner.scan:")
    )
    pending_index = next(
        index for index, event in enumerate(recorder.events)
        if event.startswith("clean_receipt.pending:")
    )
    promote_index = next(
        index for index, event in enumerate(recorder.events)
        if event.startswith("quarantine.promote:")
    )
    active_index = next(
        index for index, event in enumerate(recorder.events)
        if event.startswith("clean_receipt.record:")
    )
    validator_index = next(
        index for index, event in enumerate(recorder.events)
        if event.startswith("validator.validate:")
    )

    assert "spool.create:reservation-1" in recorder.events
    assert not any(
        event.startswith("quarantine.create:") for event in recorder.events[:scanner_index]
    )
    assert recorder.events[scanner_index - 1] == "spool.open"
    assert scanner_index < pending_index < promote_index < active_index < validator_index
    assert recorder.receipts[0].quarantine_path == (
        recorder.pending_receipts[0].formal_artifact_id
    )


def test_pending_clean_replay_promotes_without_rescanning_or_repeating_attempt() -> None:
    recorder = Recorder()
    recorder.pending_replay = pending_clean_replay()
    service, media, _ = make_service(recorder)

    service.ingest(
        request=request(),
        chunks=[b"must-not-be-read"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="evidence",
    )

    assert media.reject_reasons == []
    assert not any(event.startswith("scanner.scan:") for event in recorder.events)
    assert not any(event.startswith("scan_attempt.record:") for event in recorder.events)
    assert "spool.open_pending:spool-replay" in recorder.events
    assert "clean_receipt.record:receipt-replay:attempt-replay" in recorder.events


def test_real_sqlite_restart_resumes_after_active_commit_after_fault(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "restart.sqlite3"
    factory, engine = real_uow_factory(database_path)
    create_real_learning_session(factory)
    quarantine = LocalQuarantineStore(tmp_path / "quarantine")
    fault_factory = ActiveCommitAfterFaultFactory(factory)
    first_recorder = Recorder()
    first = make_real_service(
        first_recorder,
        factory=fault_factory,
        quarantine=quarantine,
    )

    with pytest.raises(MediaReferencePendingError, match="activation is pending"):
        first.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
        )
    engine.dispose()

    restarted_factory, restarted_engine = real_uow_factory(database_path)
    second_recorder = Recorder()
    second = make_real_service(
        second_recorder,
        factory=restarted_factory,
        quarantine=LocalQuarantineStore(tmp_path / "quarantine"),
    )

    result = second.ingest(
        request=request(),
        chunks=[b"must-not-rescan"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="evidence",
    )

    assert result.media_item_id
    assert not any(event.startswith("scanner.scan:") for event in second_recorder.events)
    with restarted_factory() as uow:
        session = uow._require_session()
        assert session.scalar(select(func.count()).select_from(MediaScanAttemptModel)) == 1
        assert session.scalar(select(func.count()).select_from(PendingCleanReceiptModel)) == 1
        assert session.scalar(select(func.count()).select_from(MediaCleanReceiptModel)) == 1
        uow.commit()
    restarted_engine.dispose()


def test_real_sqlite_stale_publication_takeover_reuses_formal_without_spool(
    tmp_path: Path,
) -> None:
    payload = b"opaque source"
    database_path = tmp_path / "formal-before-published.sqlite3"
    factory, engine = real_uow_factory(database_path)
    create_real_learning_session(factory)
    quarantine_root = tmp_path / "quarantine"
    quarantine = LocalQuarantineStore(quarantine_root)
    writer = quarantine.create_untrusted_scan_spool("reservation-before-crash")
    writer.write(payload)
    spool = writer.finalize()
    writer.close()
    attempt, _ = pending_clean_replay(payload)
    pending = quarantine.pending_clean_receipt(
        spool,
        receipt_id="receipt-before-published-crash",
        attempt_id=attempt.attempt_id,
        artifact_sha256=attempt.artifact_sha256,
        receipt_hash="f" * 64,
        created_at=attempt.finished_at,
    )
    stale_now = datetime.now(UTC) - timedelta(minutes=2)
    with factory() as uow:
        uow.scan_audit.record_attempt(attempt)
        pending = uow.scan_audit.record_pending_clean_receipt(pending)
        claim = uow.scan_audit.claim_pending_clean_publication(
            pending.receipt_id,
            owner_token="owner-before-crash",
            now=stale_now,
            lease_expires_at=stale_now + timedelta(seconds=1),
        )
        assert claim is not None
        assert claim.acquired is True
        pending = claim.pending
        uow.commit()
    formal = quarantine.promote_clean_spool(
        spool,
        receipt_id=pending.receipt_id,
        receipt_hash=pending.receipt_hash,
        formal_artifact_id=pending.formal_artifact_id,
        quarantine_expires_at=pending.quarantine_expires_at,
    )
    formal.close()
    assert tuple((quarantine_root / "untrusted-scan-spool").iterdir()) == ()
    engine.dispose()

    restarted_factory, restarted_engine = real_uow_factory(database_path)
    restarted_quarantine = LocalQuarantineStore(quarantine_root)
    recorder = Recorder()
    restarted = make_real_service(
        recorder,
        factory=restarted_factory,
        quarantine=restarted_quarantine,
    )

    result = restarted.ingest(
        request=request(),
        chunks=[b"must-not-read-or-rescan"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="recovered evidence",
    )

    assert result.media_item_id
    assert not any(event.startswith("scanner.scan:") for event in recorder.events)
    with restarted_factory() as uow:
        session = uow._require_session()
        assert session.scalar(select(func.count()).select_from(MediaScanAttemptModel)) == 1
        assert session.scalar(select(func.count()).select_from(PendingCleanReceiptModel)) == 1
        assert session.scalar(select(func.count()).select_from(MediaCleanReceiptModel)) == 1
        assert session.scalar(select(func.count()).select_from(MediaArtifactModel)) == 1
        stored_pending = session.scalar(select(PendingCleanReceiptModel))
        active = session.scalar(select(MediaCleanReceiptModel))
        assert stored_pending is not None
        assert stored_pending.publication_status == "published"
        assert active is not None
        uow.commit()
    reopened = restarted_quarantine.find_promoted_clean_receipt(
        receipt_id=active.receipt_id,
        receipt_hash=active.receipt_hash,
        artifact_sha256=active.artifact_sha256,
    )
    assert reopened is not None
    with reopened.open() as stream:
        assert stream.read() == payload
    assert len(tuple((quarantine_root / "payloads").glob("[!.]*"))) == 1
    assert len(tuple((quarantine_root / "records").glob("*.json"))) == 1
    assert len(tuple((quarantine_root / "commits").glob("*.commit"))) == 1
    restarted_engine.dispose()


def test_real_sqlite_success_keeps_active_formal_quarantine_until_expiry(
    tmp_path: Path,
) -> None:
    factory, engine = real_uow_factory(tmp_path / "single.sqlite3")
    create_real_learning_session(factory)
    quarantine = LocalQuarantineStore(tmp_path / "quarantine")
    recorder = Recorder()
    service = make_real_service(recorder, factory=factory, quarantine=quarantine)

    result = service.ingest(
        request=request(),
        chunks=[b"opaque source"],
        declared_media_type=MEDIA_TYPE,
        learner_explanation="evidence",
    )

    assert result.media_item_id
    with factory() as uow:
        session = uow._require_session()
        active = session.scalar(select(MediaCleanReceiptModel))
        assert active is not None
        uow.commit()
    promoted = quarantine.find_promoted_clean_receipt(
        receipt_id=active.receipt_id,
        receipt_hash=active.receipt_hash,
        artifact_sha256=active.artifact_sha256,
    )
    assert promoted is not None
    with promoted.open() as stream:
        assert stream.read() == b"opaque source"
    engine.dispose()


def test_real_sqlite_claim_publication_window_has_one_retryable_then_converges(
    tmp_path: Path,
) -> None:
    for round_index in range(100):
        database_path = tmp_path / f"claim-window-{round_index}.sqlite3"
        setup_factory, setup_engine = real_uow_factory(database_path)
        create_real_learning_session(setup_factory)
        setup_engine.dispose()
        owner_factory, owner_engine = real_uow_factory(database_path)
        follower_factory, follower_engine = real_uow_factory(database_path)
        quarantine_root = tmp_path / f"claim-quarantine-{round_index}"
        object_root = tmp_path / f"claim-objects-{round_index}"
        owner_jobs = tmp_path / f"claim-owner-jobs-{round_index}"
        follower_jobs = tmp_path / f"claim-follower-jobs-{round_index}"
        owner_jobs.mkdir()
        follower_jobs.mkdir()
        publication_ready = Barrier(2)
        follower_observed = Barrier(2)
        owner_reached = Event()
        owner_recorder = Recorder()
        follower_recorder = Recorder()
        owner = make_full_real_service(
            owner_recorder,
            factory=owner_factory,
            quarantine=PublicationWindowQuarantineStore(
                quarantine_root,
                publication_ready=publication_ready,
                follower_observed=follower_observed,
                owner_reached=owner_reached,
            ),
            codec=PillowImageCodecAdapter(temp_root=owner_jobs),
            object_store=LocalMediaObjectStore(object_root),
        )
        follower = make_full_real_service(
            follower_recorder,
            factory=follower_factory,
            quarantine=LocalQuarantineStore(quarantine_root),
            codec=PillowImageCodecAdapter(temp_root=follower_jobs),
            object_store=LocalMediaObjectStore(object_root),
        )

        def run_owner() -> IngestedEvidenceResult:
            return owner.ingest(
                request=request(),
                chunks=[PNG_1X1],
                declared_media_type=PNG_MEDIA_TYPE,
                learner_explanation="claim owner",
            )

        def run_follower_first() -> MediaReferencePendingError:
            publication_ready.wait(timeout=10)
            try:
                follower.ingest(
                    request=request(),
                    chunks=[b"must-not-read"],
                    declared_media_type=PNG_MEDIA_TYPE,
                    learner_explanation="claim follower",
                )
            except MediaReferencePendingError as exc:
                first = exc
            else:
                raise AssertionError("follower must observe the persisted publishing lease")
            finally:
                follower_observed.wait(timeout=10)
            return first

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                owner_future = pool.submit(run_owner)
                follower_future = pool.submit(run_follower_first)
                first_follower = follower_future.result(timeout=30)
                owner_result = owner_future.result(timeout=30)
            follower_result = follower.ingest(
                request=request(),
                chunks=[b"must-not-read"],
                declared_media_type=PNG_MEDIA_TYPE,
                learner_explanation="claim follower recovered",
            )

            assert first_follower.retryable is True
            assert owner_reached.is_set()
            assert sum(
                event.startswith("scanner.scan:")
                for recorder in (owner_recorder, follower_recorder)
                for event in recorder.events
            ) == 1
            assert not any(
                event.startswith("scanner.scan:") for event in follower_recorder.events
            )
            assert_real_convergence(
                factory=owner_factory,
                quarantine_root=quarantine_root,
                object_root=object_root,
                payload=PNG_1X1,
                results=[owner_result, follower_result],
            )
        finally:
            owner_engine.dispose()
            follower_engine.dispose()


def test_real_sqlite_published_replay_overlaps_validation_finalize_and_stage(
    tmp_path: Path,
) -> None:
    for round_index in range(100):
        for window in ("validation", "finalize", "stage"):
            database_path = tmp_path / f"published-{window}-{round_index}.sqlite3"
            quarantine_root = tmp_path / f"published-{window}-quarantine-{round_index}"
            object_root = tmp_path / f"published-{window}-objects-{round_index}"
            setup_factory, setup_engine = real_uow_factory(database_path)
            create_real_learning_session(setup_factory)
            seed_published_clean_capability(
                setup_factory,
                LocalQuarantineStore(quarantine_root),
                PNG_1X1,
            )
            setup_engine.dispose()

            factories_and_engines = (
                real_uow_factory(database_path),
                real_uow_factory(database_path),
            )
            barrier = Barrier(2)
            reached = (Event(), Event())
            recorders = (Recorder(), Recorder())
            services: list[MediaIngestionService] = []
            for index, (factory, _) in enumerate(factories_and_engines):
                jobs = tmp_path / f"published-{window}-jobs-{round_index}-{index}"
                jobs.mkdir()
                codec: object = PillowImageCodecAdapter(temp_root=jobs)
                object_store: LocalMediaObjectStore = LocalMediaObjectStore(object_root)
                service_factory: object = factory
                if window == "validation":
                    codec = ValidationBarrierCodec(codec, barrier, reached[index])  # type: ignore[arg-type]
                elif window == "finalize":
                    service_factory = FinalizeBarrierUowFactory(
                        factory,
                        barrier,
                        reached[index],
                    )
                else:
                    object_store = StageBarrierObjectStore(
                        object_root,
                        barrier,
                        reached[index],
                    )
                services.append(
                    make_full_real_service(
                        recorders[index],
                        factory=service_factory,
                        quarantine=LocalQuarantineStore(quarantine_root),
                        codec=codec,
                        object_store=object_store,
                    )
                )

            def worker(index: int) -> IngestedEvidenceResult:
                return services[index].ingest(
                    request=request(),
                    chunks=[b"must-not-read"],
                    declared_media_type=PNG_MEDIA_TYPE,
                    learner_explanation=f"published {window} participant {index}",
                )

            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(worker, range(2)))
                assert reached[0].is_set()
                assert reached[1].is_set()
                assert all(
                    not any(event.startswith("scanner.scan:") for event in recorder.events)
                    for recorder in recorders
                )
                assert_real_convergence(
                    factory=factories_and_engines[0][0],
                    quarantine_root=quarantine_root,
                    object_root=object_root,
                    payload=PNG_1X1,
                    results=results,
                )
            finally:
                for _, engine in factories_and_engines:
                    engine.dispose()


def test_real_sqlite_published_replay_stale_mark_reference_race_converges(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "published-mark-reference-race.sqlite3"
    quarantine_root = tmp_path / "published-mark-reference-quarantine"
    object_root = tmp_path / "published-mark-reference-objects"
    setup_factory, setup_engine = real_uow_factory(database_path)
    create_real_learning_session(setup_factory)
    seed_published_clean_capability(
        setup_factory,
        LocalQuarantineStore(quarantine_root),
        PNG_1X1,
    )
    setup_engine.dispose()

    owner_factory, owner_engine = real_uow_factory(database_path)
    follower_factory, follower_engine = real_uow_factory(database_path)
    stage_barrier = Barrier(1)
    stage_reached = (Event(), Event())
    race = MarkReferencedRace()
    recorders = (Recorder(), Recorder())
    owner_jobs = tmp_path / "published-mark-reference-owner-jobs"
    follower_jobs = tmp_path / "published-mark-reference-follower-jobs"
    owner_jobs.mkdir()
    follower_jobs.mkdir()

    owner = make_full_real_service(
        recorders[0],
        factory=owner_factory,
        quarantine=LocalQuarantineStore(quarantine_root),
        codec=PillowImageCodecAdapter(temp_root=owner_jobs),
        object_store=OwnerMarkRaceObjectStore(
            object_root,
            stage_barrier,
            stage_reached[0],
            race,
        ),
    )
    follower = make_full_real_service(
        recorders[1],
        factory=FollowerFinalizeAfterOwnerMarkUowFactory(follower_factory, race),
        quarantine=LocalQuarantineStore(quarantine_root),
        codec=PillowImageCodecAdapter(temp_root=follower_jobs),
        object_store=FollowerManifestRaceObjectStore(
            object_root,
            stage_barrier,
            stage_reached[1],
            race,
        ),
    )

    def run_owner() -> IngestedEvidenceResult:
        try:
            return owner.ingest(
                request=request(),
                chunks=[b"must-not-read-owner"],
                declared_media_type=PNG_MEDIA_TYPE,
                learner_explanation="published mark-reference owner",
            )
        finally:
            race.owner_completed.set()

    def run_follower() -> IngestedEvidenceResult:
        return follower.ingest(
            request=request(),
            chunks=[b"must-not-read-follower"],
            declared_media_type=PNG_MEDIA_TYPE,
            learner_explanation="published mark-reference follower",
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            follower_future = pool.submit(run_follower)
            if not race.follower_finalize_waiting.wait(timeout=30):
                follower_future.result(timeout=0)
                raise AssertionError("follower did not reach finalize")
            owner_future = pool.submit(run_owner)
            results = [
                owner_future.result(timeout=30),
                follower_future.result(timeout=30),
            ]

        assert stage_reached[0].is_set()
        assert stage_reached[1].is_set()
        assert race.owner_mark_ready.is_set()
        assert race.follower_manifest_read.is_set()
        assert race.owner_staged is not None
        assert all(
            not any(event.startswith("scanner.scan:") for event in recorder.events)
            for recorder in recorders
        )
        assert_real_convergence(
            factory=owner_factory,
            quarantine_root=quarantine_root,
            object_root=object_root,
            payload=PNG_1X1,
            results=results,
        )
    finally:
        owner_engine.dispose()
        follower_engine.dispose()


def test_pending_clean_receipt_failure_never_promotes_to_formal_quarantine() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, fail_pending_receipt=True)

    with pytest.raises(RuntimeError, match="pending receipt failed"):
        service.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
        )

    assert media.reject_reasons == ["RuntimeError"]
    assert not any(event.startswith("quarantine.promote:") for event in recorder.events)
    assert not any(event.startswith("validator.") for event in recorder.events)


def test_promote_failure_after_pending_receipt_is_retryable_without_rejecting() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, fail_promote=True)

    with pytest.raises(MediaReferencePendingError, match="promotion is pending") as caught:
        service.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
        )

    assert caught.value.retryable is True
    assert media.reject_reasons == []
    assert "spool.delete" not in recorder.events
    assert not any(event.startswith("validator.") for event in recorder.events)


def test_active_clean_receipt_failure_preserves_promoted_quarantine_for_replay() -> None:
    recorder = Recorder()
    service, media, _ = make_service(recorder, fail_active_receipt=True)

    with pytest.raises(MediaReferencePendingError, match="activation is pending") as caught:
        service.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
        )

    assert caught.value.retryable is True
    assert media.reject_reasons == []
    assert "quarantine.delete" not in recorder.events
    assert not any(event.startswith("validator.") for event in recorder.events)


def test_scanner_exception_is_sanitized_and_cleanup_preserves_primary() -> None:
    recorder = Recorder()
    service, media, _ = make_service(
        recorder, scanner_raises=RuntimeError("endpoint=/secret EICAR raw response")
    )

    with pytest.raises(MalwareScanFailedError) as caught:
        service.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
        )

    assert str(caught.value) == "media scan failed"
    assert caught.value.__cause__ is None
    assert media.reject_reasons == ["media_scan_failed"]
    assert "spool.delete" in recorder.events
    assert "spool.close" in recorder.events
    assert "quarantine.delete" not in recorder.events


def test_scanner_exception_attempt_uses_authoritative_snapshot_and_rolls_back_with_reject() -> None:
    recorder = Recorder()
    snapshot = FakeScanSnapshot(
        scanner_backend="clamd-test",
        definitions_version="daily-real-123",
        definitions_fresh_at=datetime(2026, 8, 20, 13, 30, tzinfo=UTC),
        definitions_age_seconds=91,
        max_bytes=31_457_280,
        max_concurrent_scans=11,
        deadline_ms=12_345,
        socket_timeout_ms=678,
    )
    service, media, _ = make_service(
        recorder,
        scanner_raises=RuntimeError("endpoint=/secret.sock EICAR raw bytes"),
        scanner_snapshot=snapshot,
    )

    with pytest.raises(MalwareScanFailedError) as caught:
        service.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
        )

    assert str(caught.value) == "media scan failed"
    assert len(recorder.attempts) == 1
    assert recorder.receipts == []
    attempt = recorder.attempts[0]
    assert attempt.scanner_backend == "clamd-test"
    assert attempt.definitions_version == "daily-real-123"
    assert attempt.definitions_fresh_at == datetime(2026, 8, 20, 13, 30, tzinfo=UTC)
    assert attempt.definitions_age_seconds == 91
    assert attempt.max_bytes == 31_457_280
    assert attempt.max_concurrent_scans == 11
    assert attempt.deadline_ms == 12_345
    assert attempt.socket_timeout_ms == 678
    assert attempt.scan_result.value == "error"
    assert attempt.rejection_code is not None
    assert attempt.rejection_code.value == "daemon_error"
    assert attempt.rejection_detail == "scanner exception before verdict"
    assert "secret" not in str(caught.value)
    assert media.reject_reasons == ["media_scan_failed"]


def test_audit_write_failure_rolls_back_non_clean_attempt_and_reject_together() -> None:
    recorder = Recorder()
    service, media, _ = make_service(
        recorder,
        scanner_status="error",
        fail_record_attempt=True,
    )

    with pytest.raises(RuntimeError, match="audit write failed"):
        service.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
        )

    assert recorder.attempts == []
    assert recorder.receipts == []
    assert media.reject_reasons == []
    assert "tx.reject:reservation-1:media_scan_failed" not in recorder.events


def test_scan_cancellation_propagates_after_reject_and_cleanup() -> None:
    recorder = Recorder()
    cancellation = asyncio.CancelledError()
    service, media, _ = make_service(recorder, scanner_raises=cancellation)

    with pytest.raises(asyncio.CancelledError):
        service.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
        )

    assert media.reject_reasons == ["media_scan_cancelled"]
    assert "spool.stream.close" in recorder.events
    assert "spool.delete" in recorder.events
    assert "spool.close" in recorder.events
    assert "quarantine.delete" not in recorder.events
    assert not any(event.startswith("validator.") for event in recorder.events)



def test_cancellation_gate_after_scan_blocks_validation_and_persistence() -> None:
    class CancelledGate:
        cancelled = False

        def check_active(self) -> None:
            if self.cancelled:
                raise asyncio.CancelledError

        def run_if_active(self, action):
            self.check_active()
            return action()

    gate = CancelledGate()
    recorder = Recorder()
    service, media, _ = make_service(recorder)
    original_scanner = service._malware_scanner

    class CancellingScanner:
        audit_snapshot = original_scanner.audit_snapshot

        def scan(self, source: ReadOnlyMediaSource) -> MalwareScanVerdict:
            verdict = original_scanner.scan(source)
            gate.cancelled = True
            return verdict

    service._malware_scanner = CancellingScanner()
    with pytest.raises(asyncio.CancelledError):
        service.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
            cancellation_gate=gate,
        )
    assert media.final_requests == []
    assert media.confirm_intents == []
    assert not any(
        event.startswith(("validator.", "normalizer.", "object.stage"))
        for event in recorder.events
    )



def test_cancel_before_unified_commit_blocks_all_real_ingestion_facts() -> None:
    class CancelAtLegacyFinalizeGate:
        def __init__(self) -> None:
            self.legacy_calls = 0
            self.cancelled = False

        def check_active(self) -> None:
            if self.cancelled:
                raise asyncio.CancelledError

        def run_if_active(self, action):
            self.legacy_calls += 1
            result = action()
            if self.legacy_calls == 2:
                self.cancelled = True
            return result

        def run_commit(self, action):
            self.cancelled = True
            self.check_active()
            return action()

    recorder = Recorder()
    service, media, _ = make_service(recorder)
    gate = CancelAtLegacyFinalizeGate()

    with pytest.raises(asyncio.CancelledError):
        service.ingest(
            request=request(),
            chunks=[b"opaque source"],
            declared_media_type=MEDIA_TYPE,
            learner_explanation="evidence",
            cancellation_gate=gate,
        )

    assert not any(
        event.startswith(
            (
                "object.stage",
                "tx.finalize",
                "object.mark_referenced",
                "tx.confirm",
            )
        )
        for event in recorder.events
    )
    assert media.final_requests == []
    assert media.confirm_intents == []



def test_concurrent_cancel_after_validate_wins_before_commit() -> None:
    import threading

    recorder = Recorder()
    service, media, _ = make_service(recorder)
    gate = ThreadSafeMediaCancellationGate()
    entered = threading.Event()
    release = threading.Event()
    original_validate = service._validator.validate

    def blocking_validate(source, declared_media_type):
        result = original_validate(source, declared_media_type)
        entered.set()
        release.wait(timeout=2)
        return result

    service._validator.validate = blocking_validate
    failures: list[BaseException] = []

    def ingest() -> None:
        try:
            service.ingest(
                request=request(),
                chunks=[b"opaque source"],
                declared_media_type=MEDIA_TYPE,
                learner_explanation="evidence",
                cancellation_gate=gate,
            )
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=ingest)
    worker.start()
    assert entered.wait(timeout=1)
    assert gate.cancel() is True
    release.set()
    worker.join(timeout=2)
    assert len(failures) == 1
    assert type(failures[0]).__name__ == "MediaUploadCancelled"
    assert media.final_requests == []
    assert media.confirm_intents == []
    assert not any(
        event.startswith(("object.stage", "object.mark_referenced"))
        for event in recorder.events
    )


def test_concurrent_cancel_wins_immediately_before_unified_commit() -> None:
    import threading

    class PausingCommitGate(ThreadSafeMediaCancellationGate):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def run_commit(self, action):
            self.entered.set()
            self.release.wait(timeout=2)
            return super().run_commit(action)

    recorder = Recorder()
    service, media, _ = make_service(recorder)
    gate = PausingCommitGate()
    failures: list[BaseException] = []

    def ingest() -> None:
        try:
            service.ingest(
                request=request(),
                chunks=[b"opaque source"],
                declared_media_type=MEDIA_TYPE,
                learner_explanation="evidence",
                cancellation_gate=gate,
            )
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=ingest)
    worker.start()
    assert gate.entered.wait(timeout=1)
    assert gate.cancel() is True
    gate.release.set()
    worker.join(timeout=2)
    assert len(failures) == 1
    assert type(failures[0]).__name__ == "MediaUploadCancelled"
    assert media.final_requests == []
    assert media.confirm_intents == []
    assert not any(
        event.startswith(("object.stage", "object.mark_referenced"))
        for event in recorder.events
    )


@pytest.mark.parametrize("phase", ["stage", "finalize", "mark", "confirm"])
def test_commit_linearizes_before_late_concurrent_cancel(
    phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    recorder = Recorder()
    service, media, object_store = make_service(recorder)
    gate = ThreadSafeMediaCancellationGate()
    entered = threading.Event()
    release = threading.Event()
    cancel_results: list[bool] = []
    outcomes: list[IngestedEvidenceResult] = []
    failures: list[BaseException] = []

    if phase == "stage":
        original = object_store.stage

        def blocking_stage(*args, **kwargs):
            entered.set()
            release.wait(timeout=2)
            return original(*args, **kwargs)

        monkeypatch.setattr(object_store, "stage", blocking_stage)
    elif phase == "finalize":
        original = type(media).finalize

        def blocking_finalize(self, request):
            entered.set()
            release.wait(timeout=2)
            return original(self, request)

        monkeypatch.setattr(type(media), "finalize", blocking_finalize)
    elif phase == "mark":
        original = object_store.mark_referenced

        def blocking_mark(staged):
            entered.set()
            release.wait(timeout=2)
            return original(staged)

        monkeypatch.setattr(object_store, "mark_referenced", blocking_mark)
    else:
        original = type(media).confirm_reference

        def blocking_confirm(self, intent):
            entered.set()
            release.wait(timeout=2)
            return original(self, intent)

        monkeypatch.setattr(type(media), "confirm_reference", blocking_confirm)

    def ingest() -> None:
        try:
            outcomes.append(
                service.ingest(
                    request=request(),
                    chunks=[b"opaque source"],
                    declared_media_type=MEDIA_TYPE,
                    learner_explanation="evidence",
                    cancellation_gate=gate,
                )
            )
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=ingest)
    worker.start()
    assert entered.wait(timeout=1)
    canceller = threading.Thread(target=lambda: cancel_results.append(gate.cancel()))
    canceller.start()
    canceller.join(timeout=0.02)
    assert canceller.is_alive()
    release.set()
    worker.join(timeout=2)
    canceller.join(timeout=2)

    assert failures == []
    assert len(outcomes) == 1
    assert cancel_results == [False]
    assert len(media.final_requests) == 1
    assert len(media.confirm_intents) == 1
    assert any(event.startswith("object.mark_referenced") for event in recorder.events)
