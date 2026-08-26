from __future__ import annotations

import builtins
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from focusproof.contracts.media_scan import (
    ScanRejectionCode,
    ScanResultKind,
)
from focusproof.persistence.models import (
    MediaCleanReceiptModel,
    MediaScanAttemptModel,
    PendingCleanReceiptModel,
)
from focusproof.persistence.repositories import StoredAuditEvent
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike
from focusproof.runtime.events import Actor, Event, EventType

if TYPE_CHECKING:
    from focusproof.media_core.models import (
        CleanReceiptPublicationClaim,
        MediaCleanReceipt,
        MediaScanAttempt,
        PendingCleanReceipt,
        PublicationStatus,
    )


class MediaScanAuditRepository:
    """Replay-safe persistence for scan attempts and genuine clean receipts."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_attempt(self, attempt: MediaScanAttempt) -> MediaScanAttempt:
        try:
            with self._session.begin_nested():
                self._session.add(MediaScanAttemptModel(**_attempt_values(attempt)))
                self._session.flush()
            return attempt
        except IntegrityError:
            pass
        existing = self._session.scalar(
            select(MediaScanAttemptModel).where(
                MediaScanAttemptModel.idempotency_key == attempt.idempotency_key
            )
        )
        if existing is not None:
            stored = _scan_attempt(existing)
            if _same_scan_attempt_identity(stored, attempt):
                return stored
            raise IntegrityError(
                "idempotency key belongs to a different scan attempt",
                {"idempotency_key": attempt.idempotency_key},
                ValueError("idempotency conflict"),
            )
        raise IntegrityError(
            "idempotency key belongs to a different scan attempt",
            {"idempotency_key": attempt.idempotency_key},
            ValueError("idempotency conflict"),
        )

    def record_pending_clean_receipt(self, pending: PendingCleanReceipt) -> PendingCleanReceipt:
        attempt_model = self._session.get(MediaScanAttemptModel, pending.attempt_id)
        if attempt_model is None or attempt_model.scan_result != ScanResultKind.CLEAN.value:
            raise IntegrityError(
                "pending clean receipt requires a persisted clean scan attempt",
                {"attempt_id": pending.attempt_id},
                ValueError("missing clean attempt"),
            )
        if attempt_model.artifact_sha256 != pending.artifact_sha256:
            raise IntegrityError(
                "pending clean receipt snapshot differs from its scan attempt",
                {"attempt_id": pending.attempt_id},
                ValueError("snapshot mismatch"),
            )
        try:
            with self._session.begin_nested():
                self._session.add(PendingCleanReceiptModel(**_pending_values(pending)))
                self._session.flush()
            return pending
        except IntegrityError:
            pass
        existing = self._session.scalar(
            select(PendingCleanReceiptModel).where(
                PendingCleanReceiptModel.attempt_id == pending.attempt_id
            )
        )
        if existing is not None:
            stored = _pending_clean_receipt(existing)
            if _same_pending_clean_receipt_identity(stored, pending):
                return stored
        raise IntegrityError(
            "scan attempt already has a different pending clean receipt",
            {"attempt_id": pending.attempt_id},
            ValueError("pending receipt conflict"),
        )

    def record_clean_receipt(self, receipt: MediaCleanReceipt) -> MediaCleanReceipt:
        from focusproof.media_core.models import MediaCleanReceipt

        pending_model = self._session.get(PendingCleanReceiptModel, receipt.receipt_id)
        if pending_model is None:
            raise IntegrityError(
                "active clean receipt requires a pending clean receipt",
                {"receipt_id": receipt.receipt_id},
                ValueError("missing pending receipt"),
            )
        pending = _pending_clean_receipt(pending_model)
        if (
            pending.attempt_id != receipt.attempt_id
            or pending.artifact_sha256 != receipt.artifact_sha256
            or pending.receipt_hash != receipt.receipt_hash
        ):
            raise IntegrityError(
                "active clean receipt differs from pending clean receipt",
                {"receipt_id": receipt.receipt_id},
                ValueError("pending receipt mismatch"),
            )
        attempt_model = self._session.get(MediaScanAttemptModel, receipt.attempt_id)
        if attempt_model is None or attempt_model.scan_result != ScanResultKind.CLEAN.value:
            raise IntegrityError(
                "clean receipt requires a persisted clean scan attempt",
                {"attempt_id": receipt.attempt_id},
                ValueError("missing clean attempt"),
            )
        attempt = _scan_attempt(attempt_model)
        expected_snapshot = (
            attempt.artifact_sha256,
            attempt.scanner_backend,
            attempt.definitions_version,
            attempt.definitions_fresh_at,
            attempt.definitions_age_seconds,
            attempt.max_bytes,
            attempt.max_concurrent_scans,
            attempt.deadline_ms,
            attempt.socket_timeout_ms,
        )
        receipt_snapshot = (
            receipt.artifact_sha256,
            receipt.scanner_backend,
            receipt.definitions_version,
            receipt.definitions_fresh_at,
            receipt.definitions_age_seconds,
            receipt.max_bytes,
            receipt.max_concurrent_scans,
            receipt.deadline_ms,
            receipt.socket_timeout_ms,
        )
        if receipt_snapshot != expected_snapshot:
            raise IntegrityError(
                "clean receipt snapshot differs from its scan attempt",
                {"attempt_id": receipt.attempt_id},
                ValueError("snapshot mismatch"),
            )
        stable_receipt = MediaCleanReceipt.from_attempt(
            attempt,
            receipt_id=receipt.receipt_id,
            receipt_hash=receipt.receipt_hash,
            quarantine_path=receipt.quarantine_path,
            quarantine_expires_at=receipt.quarantine_expires_at,
            created_at=pending.created_at,
        )
        try:
            with self._session.begin_nested():
                self._session.add(MediaCleanReceiptModel(**_receipt_values(stable_receipt)))
                self._session.flush()
            return stable_receipt
        except IntegrityError:
            pass
        existing = self._session.scalar(
            select(MediaCleanReceiptModel).where(
                MediaCleanReceiptModel.attempt_id == receipt.attempt_id
            )
        )
        if existing is not None:
            stored = _clean_receipt(existing)
            if _same_clean_receipt_identity(stored, stable_receipt):
                return stored
        raise IntegrityError(
            "scan attempt already has a different clean receipt",
            {"attempt_id": receipt.attempt_id},
            ValueError("receipt conflict"),
        )

    def claim_pending_clean_publication(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> CleanReceiptPublicationClaim | None:
        from focusproof.media_core.models import CleanReceiptPublicationClaim

        if not owner_token.strip():
            raise ValueError("publication owner token must not be blank")
        if lease_expires_at <= now:
            raise ValueError("publication lease must expire after now")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(PendingCleanReceiptModel)
            .where(
                PendingCleanReceiptModel.receipt_id == receipt_id,
                (
                    PendingCleanReceiptModel.publication_status.in_(("pending", "failed"))
                    | (
                        (PendingCleanReceiptModel.publication_status == "publishing")
                        & (
                            PendingCleanReceiptModel.publication_lease_expires_at
                            <= now
                        )
                    )
                ),
            )
            .values(
                publication_status="publishing",
                publication_owner=owner_token,
                publication_lease_expires_at=lease_expires_at,
                publication_version=PendingCleanReceiptModel.publication_version + 1,
                publication_failure=None,
                updated_at=now,
            )
            ),
        )
        pending = self._get_pending_clean_receipt_locked(receipt_id)
        if pending is None:
            return None
        return CleanReceiptPublicationClaim(
            acquired=bool(result.rowcount),
            pending=_pending_clean_receipt(pending),
        )

    def refresh_pending_clean_publication_lease(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> PendingCleanReceipt | None:
        if not owner_token.strip():
            raise ValueError("publication owner token must not be blank")
        if lease_expires_at <= now:
            raise ValueError("publication lease must expire after now")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(PendingCleanReceiptModel)
            .where(
                PendingCleanReceiptModel.receipt_id == receipt_id,
                PendingCleanReceiptModel.publication_status == "publishing",
                PendingCleanReceiptModel.publication_owner == owner_token,
            )
            .values(
                publication_lease_expires_at=lease_expires_at,
                publication_version=PendingCleanReceiptModel.publication_version + 1,
                updated_at=now,
            )
            ),
        )
        if not result.rowcount:
            return None
        pending = self._get_pending_clean_receipt_locked(receipt_id)
        return _pending_clean_receipt(pending) if pending is not None else None

    def mark_pending_clean_publication_published(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        formal_artifact_id: str,
        now: datetime,
    ) -> PendingCleanReceipt:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(PendingCleanReceiptModel)
            .where(
                PendingCleanReceiptModel.receipt_id == receipt_id,
                PendingCleanReceiptModel.publication_status == "publishing",
                PendingCleanReceiptModel.publication_owner == owner_token,
                PendingCleanReceiptModel.formal_artifact_id == formal_artifact_id,
            )
            .values(
                publication_status="published",
                publication_owner=None,
                publication_lease_expires_at=None,
                publication_version=PendingCleanReceiptModel.publication_version + 1,
                published_at=now,
                publication_failure=None,
                updated_at=now,
            )
            ),
        )
        pending = self._get_pending_clean_receipt_locked(receipt_id)
        if pending is None:
            raise IntegrityError(
                "published clean receipt requires a pending clean receipt",
                {"receipt_id": receipt_id},
                ValueError("missing pending receipt"),
            )
        stored = _pending_clean_receipt(pending)
        if result.rowcount:
            return stored
        if (
            stored.publication_status == "published"
            and stored.formal_artifact_id == formal_artifact_id
        ):
            return stored
        raise IntegrityError(
            "pending clean receipt publication owner changed",
            {"receipt_id": receipt_id},
            ValueError("publication owner mismatch"),
        )

    def mark_pending_clean_publication_failed(
        self,
        receipt_id: str,
        *,
        owner_token: str,
        now: datetime,
        reason: str,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(PendingCleanReceiptModel)
            .where(
                PendingCleanReceiptModel.receipt_id == receipt_id,
                PendingCleanReceiptModel.publication_status == "publishing",
                PendingCleanReceiptModel.publication_owner == owner_token,
            )
            .values(
                publication_status="failed",
                publication_owner=None,
                publication_lease_expires_at=None,
                publication_version=PendingCleanReceiptModel.publication_version + 1,
                publication_failure=(reason or type(reason).__name__)[:128],
                updated_at=now,
            )
            ),
        )
        return bool(result.rowcount)

    def find_pending_clean_receipt(
        self,
        idempotency_key: str,
    ) -> tuple[MediaScanAttempt, PendingCleanReceipt] | None:
        row = self._session.execute(
            select(MediaScanAttemptModel, PendingCleanReceiptModel)
            .join(
                PendingCleanReceiptModel,
                PendingCleanReceiptModel.attempt_id == MediaScanAttemptModel.attempt_id,
            )
            .where(MediaScanAttemptModel.idempotency_key == idempotency_key)
        ).one_or_none()
        if row is None:
            return None
        attempt_model, pending_model = row
        return _scan_attempt(attempt_model), _pending_clean_receipt(pending_model)

    def list_expired_pending_clean_receipts(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[PendingCleanReceipt, ...]:
        if limit <= 0:
            return ()
        rows = self._session.scalars(
            select(PendingCleanReceiptModel)
            .outerjoin(
                MediaCleanReceiptModel,
                MediaCleanReceiptModel.receipt_id == PendingCleanReceiptModel.receipt_id,
            )
            .where(PendingCleanReceiptModel.spool_expires_at <= now)
            .where(MediaCleanReceiptModel.receipt_id.is_(None))
            .where(PendingCleanReceiptModel.publication_status.in_(("pending", "failed")))
            .order_by(PendingCleanReceiptModel.created_at, PendingCleanReceiptModel.receipt_id)
            .limit(limit)
        ).all()
        return tuple(_pending_clean_receipt(row) for row in rows)

    def delete_pending_clean_receipt(self, receipt_id: str) -> bool:
        if self._session.get(MediaCleanReceiptModel, receipt_id) is not None:
            return False
        pending = self._session.get(PendingCleanReceiptModel, receipt_id)
        if pending is None:
            return False
        self._session.delete(pending)
        return True

    def _get_pending_clean_receipt_locked(
        self,
        receipt_id: str,
    ) -> PendingCleanReceiptModel | None:
        return self._session.scalar(
            select(PendingCleanReceiptModel)
            .where(PendingCleanReceiptModel.receipt_id == receipt_id)
            .with_for_update()
        )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _attempt_values(attempt: MediaScanAttempt) -> dict[str, object]:
    return {field: getattr(attempt, field) for field in attempt.__dataclass_fields__} | {
        "scan_result": attempt.scan_result.value,
        "rejection_code": attempt.rejection_code.value if attempt.rejection_code else None,
    }


def _receipt_values(receipt: MediaCleanReceipt) -> dict[str, object]:
    return {field: getattr(receipt, field) for field in receipt.__dataclass_fields__}


def _pending_values(pending: PendingCleanReceipt) -> dict[str, object]:
    return {field: getattr(pending, field) for field in pending.__dataclass_fields__}


def _same_pending_clean_receipt_identity(
    left: PendingCleanReceipt,
    right: PendingCleanReceipt,
) -> bool:
    return (
        left.receipt_id == right.receipt_id
        and left.attempt_id == right.attempt_id
        and left.artifact_sha256 == right.artifact_sha256
        and left.receipt_hash == right.receipt_hash
        and left.formal_artifact_id == right.formal_artifact_id
    )


def _same_scan_attempt_identity(left: MediaScanAttempt, right: MediaScanAttempt) -> bool:
    return (
        left.attempt_id == right.attempt_id
        and left.artifact_sha256 == right.artifact_sha256
        and left.content_type == right.content_type
        and left.scanner_backend == right.scanner_backend
        and left.definitions_version == right.definitions_version
        and left.definitions_fresh_at == right.definitions_fresh_at
        and left.definitions_age_seconds == right.definitions_age_seconds
        and left.max_bytes == right.max_bytes
        and left.max_concurrent_scans == right.max_concurrent_scans
        and left.deadline_ms == right.deadline_ms
        and left.socket_timeout_ms == right.socket_timeout_ms
        and left.scan_result == right.scan_result
        and left.rejection_code == right.rejection_code
        and left.rejection_detail == right.rejection_detail
        and left.idempotency_key == right.idempotency_key
    )


def _same_clean_receipt_identity(left: MediaCleanReceipt, right: MediaCleanReceipt) -> bool:
    return (
        left.receipt_id == right.receipt_id
        and left.attempt_id == right.attempt_id
        and left.artifact_sha256 == right.artifact_sha256
        and left.receipt_hash == right.receipt_hash
        and left.scanner_backend == right.scanner_backend
        and left.definitions_version == right.definitions_version
        and left.definitions_fresh_at == right.definitions_fresh_at
        and left.definitions_age_seconds == right.definitions_age_seconds
        and left.max_bytes == right.max_bytes
        and left.max_concurrent_scans == right.max_concurrent_scans
        and left.deadline_ms == right.deadline_ms
        and left.socket_timeout_ms == right.socket_timeout_ms
        and left.quarantine_path == right.quarantine_path
        and left.quarantine_expires_at == right.quarantine_expires_at
    )


def _scan_attempt(model: MediaScanAttemptModel) -> MediaScanAttempt:
    from focusproof.media_core.models import MediaScanAttempt

    return MediaScanAttempt(
        attempt_id=model.attempt_id,
        artifact_sha256=model.artifact_sha256,
        content_type=model.content_type,
        scanner_backend=model.scanner_backend,
        definitions_version=model.definitions_version,
        definitions_fresh_at=_as_utc(model.definitions_fresh_at),
        definitions_age_seconds=model.definitions_age_seconds,
        max_bytes=model.max_bytes,
        max_concurrent_scans=model.max_concurrent_scans,
        deadline_ms=model.deadline_ms,
        socket_timeout_ms=model.socket_timeout_ms,
        scan_result=ScanResultKind(model.scan_result),
        rejection_code=(ScanRejectionCode(model.rejection_code) if model.rejection_code else None),
        rejection_detail=model.rejection_detail,
        started_at=_as_utc(model.started_at),
        finished_at=_as_utc(model.finished_at),
        idempotency_key=model.idempotency_key,
    )


def _clean_receipt(model: MediaCleanReceiptModel) -> MediaCleanReceipt:
    from focusproof.media_core.models import MediaCleanReceipt

    return MediaCleanReceipt(
        receipt_id=model.receipt_id,
        attempt_id=model.attempt_id,
        artifact_sha256=model.artifact_sha256,
        receipt_hash=model.receipt_hash,
        scanner_backend=model.scanner_backend,
        definitions_version=model.definitions_version,
        definitions_fresh_at=_as_utc(model.definitions_fresh_at),
        definitions_age_seconds=model.definitions_age_seconds,
        max_bytes=model.max_bytes,
        max_concurrent_scans=model.max_concurrent_scans,
        deadline_ms=model.deadline_ms,
        socket_timeout_ms=model.socket_timeout_ms,
        quarantine_path=model.quarantine_path,
        quarantine_expires_at=_as_utc(model.quarantine_expires_at),
        created_at=_as_utc(model.created_at),
    )


def _pending_clean_receipt(model: PendingCleanReceiptModel) -> PendingCleanReceipt:
    from focusproof.media_core.models import PendingCleanReceipt

    return PendingCleanReceipt(
        receipt_id=model.receipt_id,
        attempt_id=model.attempt_id,
        artifact_sha256=model.artifact_sha256,
        receipt_hash=model.receipt_hash,
        spool_token=model.spool_token,
        spool_byte_size=model.spool_byte_size,
        spool_sha256=model.spool_sha256,
        spool_expires_at=_as_utc(model.spool_expires_at),
        quarantine_expires_at=_as_utc(model.quarantine_expires_at),
        created_at=_as_utc(model.created_at),
        formal_artifact_id=model.formal_artifact_id,
        publication_status=cast("PublicationStatus", model.publication_status),
        publication_owner=model.publication_owner,
        publication_lease_expires_at=(
            _as_utc(model.publication_lease_expires_at)
            if model.publication_lease_expires_at is not None
            else None
        ),
        publication_version=model.publication_version,
        published_at=(_as_utc(model.published_at) if model.published_at is not None else None),
        publication_failure=model.publication_failure,
        updated_at=_as_utc(model.updated_at),
    )


class PersistentAuditProjectionStore:
    """Durable FocusProof query projection of official OpenHands events."""

    def __init__(self, uow_factory: UnitOfWorkFactoryLike) -> None:
        self._uow_factory = uow_factory

    def append(
        self,
        session_id: str,
        event_type: EventType,
        actor: Actor,
        payload: dict[str, object],
    ) -> Event:
        return self._append(session_id, event_type, actor, payload)

    def append_final(
        self,
        session_id: str,
        event_type: EventType,
        actor: Actor,
        payload: dict[str, object],
        *,
        event_id: str,
    ) -> Event:
        return self._append(session_id, event_type, actor, payload, event_id=event_id)

    def _append(
        self,
        session_id: str,
        event_type: EventType,
        actor: Actor,
        payload: dict[str, object],
        *,
        event_id: str | None = None,
    ) -> Event:
        source_id = payload.get("sourceOpenHandsEventId")
        with self._uow_factory() as uow:
            stored = uow.audit_events.append(
                session_id,
                event_type,
                actor,
                dict(payload),
                source_openhands_event_id=(
                    source_id if isinstance(source_id, str) else None
                ),
                event_id=event_id,
            )
            uow.commit()
        return _runtime_event(stored)

    def list(self, session_id: str) -> builtins.list[Event]:
        with self._uow_factory() as uow:
            return [_runtime_event(event) for event in uow.audit_events.list(session_id)]

    def latest(self, session_id: str) -> Event | None:
        with self._uow_factory() as uow:
            event = uow.audit_events.latest(session_id)
        return _runtime_event(event) if event is not None else None

    def has_source_event(self, session_id: str, source_event_id: str) -> bool:
        with self._uow_factory() as uow:
            return uow.audit_events.has_source_event(session_id, source_event_id)

    def get_by_type(
        self,
        session_id: str,
        event_type: EventType,
    ) -> builtins.list[Event]:
        return [event for event in self.list(session_id) if event.type == event_type]

    def count(self, session_id: str) -> int:
        return len(self.list(session_id))


def _runtime_event(stored: StoredAuditEvent) -> Event:
    return Event(
        id=stored.event_id,
        sessionId=stored.session_id,
        type=cast(EventType, stored.type),
        sequence=stored.sequence,
        createdAt=stored.created_at.isoformat(),
        actor=cast(Actor, stored.actor),
        payload=stored.payload,
    )
