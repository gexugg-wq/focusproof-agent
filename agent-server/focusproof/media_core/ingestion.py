from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import ClassVar
from uuid import NAMESPACE_URL, uuid4, uuid5

from focusproof.contracts.media_scan import default_scan_rejection_code
from focusproof.media_core.limits import CanonicalMessageByteLimit, SourceByteLimit
from focusproof.media_core.models import (
    CleanReceiptPublicationClaim,
    FinalizeMediaOutcome,
    FinalizeMediaRequest,
    IngestedEvidenceResult,
    MediaLease,
    MediaCleanReceipt,
    MediaScanAttempt,
    MediaReferenceIntent,
    MediaReservationRequest,
    PendingCleanReceipt,
    ScanResultKind,
    freeze_attributes,
)
from focusproof.media_core.ports import (
    MediaCancellationGate,
    MalwareScanner,
    MalwareScanVerdict,
    malware_rejection_code,
    MediaNormalizer,
    MediaObjectStore,
    MediaUnitOfWorkFactory,
    MediaValidator,
    NormalizedMediaSource,
    QuarantineStore,
    QuarantineWriter,
    ReadOnlyMediaSource,
    ReadOnlyQuarantineObject,
    StagedMediaObject,
    ValidatedMediaMetadata,
)


class MalwareDetectedError(ValueError):
    pass


class MalwareScanUnavailableError(RuntimeError):
    pass


class MalwareScanTimeoutError(RuntimeError):
    pass


class MalwareScanFailedError(RuntimeError):
    pass


class MalwareScanUnknownError(RuntimeError):
    pass


_SCAN_ERRORS: dict[str, type[Exception]] = {
    "media_malware_detected": MalwareDetectedError,
    "media_scan_unavailable": MalwareScanUnavailableError,
    "media_scan_timeout": MalwareScanTimeoutError,
    "media_scan_failed": MalwareScanFailedError,
    "media_scan_unknown": MalwareScanUnknownError,
}
_PUBLICATION_LEASE_SECONDS = 30


class QuarantineMetadataMismatchError(ValueError):
    """Raised when a finalized quarantine object disagrees with streamed facts."""


class ValidatedMetadataMismatchError(ValueError):
    """Raised when validation metadata disagrees with authoritative source facts."""


class PostCommitReferenceError(RuntimeError):
    """Raised after DB commit when durable object reference marking needs repair."""


class InvalidMediaReferenceOutcomeError(ValueError):
    """Raised when a transaction returns an impossible reference outcome."""


class MediaReferencePendingError(RuntimeError):
    """Raised when a committed media reference is pending and should be retried."""

    retryable: ClassVar[bool] = True


class MediaPromotionPendingError(MediaReferencePendingError):
    """Raised after clean scan persistence when promotion/activation must be retried."""


class CleanupError(RuntimeError):
    """Aggregates cleanup failures without replacing the primary exception."""

    def __init__(self, errors: list[BaseException]) -> None:
        super().__init__("media cleanup failed")
        self.errors = tuple(errors)


@dataclass(frozen=True, slots=True)
class _CleanScanIntention:
    attempt: MediaScanAttempt
    pending: PendingCleanReceipt


class MediaIngestionService:
    def __init__(
        self,
        *,
        uow_factory: MediaUnitOfWorkFactory,
        quarantine_store: QuarantineStore,
        malware_scanner: MalwareScanner,
        validator: MediaValidator,
        normalizer: MediaNormalizer,
        object_store: MediaObjectStore,
        source_byte_limit: SourceByteLimit | None = None,
        canonical_message_byte_limit: CanonicalMessageByteLimit | None = None,
        cleanup_error_reporter: Callable[[CleanupError], None],
        session_lock_acquire: Callable[[str], AbstractContextManager[None]] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._quarantine_store = quarantine_store
        self._malware_scanner = malware_scanner
        self._validator = validator
        self._normalizer = normalizer
        self._object_store = object_store
        self._source_byte_limit = source_byte_limit or SourceByteLimit()
        self._canonical_message_byte_limit = (
            canonical_message_byte_limit or CanonicalMessageByteLimit()
        )
        self._cleanup_error_reporter = cleanup_error_reporter
        self._session_lock_acquire = session_lock_acquire

    def ingest(
        self,
        *,
        request: MediaReservationRequest,
        chunks: Iterable[bytes],
        declared_media_type: str | None,
        learner_explanation: str,
        cancellation_gate: MediaCancellationGate | None = None,
    ) -> IngestedEvidenceResult:
        cleanup_errors: list[BaseException] = []
        self._check_active(cancellation_gate)
        replay = self._find_replay(request)
        if replay is not None:
            replay_outcome = replay
            try:
                self._check_active(cancellation_gate)

                def replay_action() -> IngestedEvidenceResult:
                    return self._complete_replay_reference_intent(
                        replay_outcome,
                        cleanup_errors,
                        request,
                    )

                return self._run_final_publication(
                    request.session_id,
                    replay_action,
                    cancellation_gate,
                )
            finally:
                self._publish_cleanup_errors(cleanup_errors)

        pending_replay = self._find_pending_clean_intention(request)
        try:
            lease = self._reserve(request)
        except MediaReferencePendingError:
            replay = self._find_replay(request)
            if replay is not None:
                assert replay is not None
                replay_outcome = replay
                try:
                    self._check_active(cancellation_gate)

                    def replay_action() -> IngestedEvidenceResult:
                        return self._complete_replay_reference_intent(
                            replay_outcome,
                            cleanup_errors,
                            request,
                        )

                    return self._run_final_publication(
                        request.session_id,
                        replay_action,
                        cancellation_gate,
                    )
                finally:
                    self._publish_cleanup_errors(cleanup_errors)
            raise
        writer: QuarantineWriter | None = None
        writer_close_attempted = False
        spool: ReadOnlyQuarantineObject | None = None
        quarantine: ReadOnlyQuarantineObject | None = None
        normalized: NormalizedMediaSource | None = None
        staged: StagedMediaObject | None = None
        committed_outcome: FinalizeMediaOutcome | None = None
        primary: BaseException | None = None
        try:
            if pending_replay is None:
                writer = self._quarantine_store.create_untrusted_scan_spool(lease.reservation_id)
                digest = sha256()
                byte_size = 0
                for chunk in chunks:
                    byte_size += len(chunk)
                    self._source_byte_limit.check(byte_size)
                    digest.update(chunk)
                    writer.write(chunk)

                source_sha256 = digest.hexdigest()
                spool = writer.finalize()
                writer_close_attempted = True
                self._run_cleanup(cleanup_errors, writer.close)
                self._assert_quarantine_facts(spool, byte_size, source_sha256)
                intention = self._scan_and_record(
                    lease, spool, source_sha256, byte_size, declared_media_type
                )
            else:
                intention = pending_replay
                source_sha256 = intention.pending.artifact_sha256
                byte_size = intention.pending.spool_byte_size
            self._check_active(cancellation_gate)
            promote_spool = spool
            if spool is not None and spool.quarantine_id != intention.pending.spool_token:
                self._run_cleanup(cleanup_errors, spool.delete)
                self._run_cleanup(cleanup_errors, spool.close)
                promote_spool = None
                spool = None
            quarantine = self._promote_pending_clean_receipt(intention, promote_spool)
            spool = None
            try:
                self._activate_clean_receipt(intention, quarantine)
            except Exception as exc:
                raise MediaPromotionPendingError(
                    "clean receipt activation is pending; retry with idempotency key"
                ) from exc
            self._check_active(cancellation_gate)

            metadata = self._validate(quarantine, source_sha256, byte_size, declared_media_type)
            self._check_active(cancellation_gate)
            normalized = self._normalize(quarantine, metadata)
            normalized_sha256, normalized_byte_size = self._verify_normalized(normalized)
            self._canonical_message_byte_limit.check(normalized_byte_size)
            self._check_active(cancellation_gate)
            normalized.rewind()

            def commit_sequence() -> IngestedEvidenceResult:
                nonlocal staged
                staged = self._object_store.stage(
                    normalized, lease.media_item_id, lease.reservation_id
                )

                def publish_sequence() -> IngestedEvidenceResult:
                    nonlocal committed_outcome
                    assert staged is not None
                    committed_outcome = self._finalize(
                        FinalizeMediaRequest(
                            lease=lease,
                            staged_media_item_id=staged.media_item_id,
                            opaque_object_key=staged.opaque_object_key,
                            manifest_id=staged.manifest_id,
                            media_type=metadata.media_type,
                            normalized_sha256=normalized_sha256,
                            normalized_byte_size=normalized_byte_size,
                            learner_explanation=learner_explanation,
                            attributes=metadata.attributes,
                        )
                    )
                    return self._complete_fresh_reference_intent(
                        committed_outcome, staged, lease, cleanup_errors
                    )

                return self._run_session_publication(
                    request.session_id,
                    publish_sequence,
                )

            return (
                commit_sequence()
                if cancellation_gate is None
                else cancellation_gate.run_commit(commit_sequence)
            )
        except BaseException as exc:
            primary = exc
            retryable_pending = getattr(exc, "retryable", False)
            if retryable_pending:
                spool = None
                quarantine = None
            if isinstance(exc, BaseExceptionGroup):
                reason = "media_scan_failed"
            elif type(exc).__name__ == "CancelledError":
                reason = "media_scan_cancelled"
            elif isinstance(exc, tuple(_SCAN_ERRORS.values())):
                reason = next(
                    code for code, error_type in _SCAN_ERRORS.items() if isinstance(exc, error_type)
                )
            else:
                reason = type(exc).__name__
            if committed_outcome is None:
                if writer is not None and quarantine is None and not retryable_pending:
                    self._run_cleanup(cleanup_errors, writer.abort)
                if staged is not None:
                    self._run_cleanup(
                        cleanup_errors, lambda: self._object_store.abort_staged(staged)
                    )
                if not getattr(exc, "scan_rejection_committed", False) and not retryable_pending:
                    self._run_cleanup(cleanup_errors, lambda: self._reject(lease, reason))
            raise
        finally:
            if writer is not None and not writer_close_attempted:
                self._run_cleanup(cleanup_errors, writer.close)
            if normalized is not None:
                self._run_cleanup(cleanup_errors, normalized.close)
            if quarantine is not None:
                self._run_cleanup(cleanup_errors, quarantine.close)
            if spool is not None:
                self._run_cleanup(cleanup_errors, spool.delete)
                self._run_cleanup(cleanup_errors, spool.close)
            self._publish_cleanup_errors(cleanup_errors)
            if primary is not None:
                self._attach_cleanup_notes(primary, cleanup_errors)

    @staticmethod
    def _check_active(cancellation_gate: MediaCancellationGate | None) -> None:
        if cancellation_gate is not None:
            cancellation_gate.check_active()

    def _run_final_publication(
        self,
        session_id: str,
        action: Callable[[], IngestedEvidenceResult],
        cancellation_gate: MediaCancellationGate | None,
    ) -> IngestedEvidenceResult:
        def publish() -> IngestedEvidenceResult:
            return action() if cancellation_gate is None else cancellation_gate.run_commit(action)

        return self._run_session_publication(session_id, publish)

    def _run_session_publication(
        self,
        session_id: str,
        action: Callable[[], IngestedEvidenceResult],
    ) -> IngestedEvidenceResult:

        if self._session_lock_acquire is None:
            return action()
        with self._session_lock_acquire(session_id):
            return action()

    def _find_replay(self, request: MediaReservationRequest) -> FinalizeMediaOutcome | None:
        with self._uow_factory() as uow:
            outcome = uow.media.find_idempotent_outcome(
                request.owner_id,
                request.session_id,
                request.idempotency_key,
                request.fingerprint,
            )
            uow.commit()
            return outcome

    def _find_pending_clean_intention(
        self,
        request: MediaReservationRequest,
    ) -> _CleanScanIntention | None:
        stable_key = self._scan_idempotency_key(request)
        with self._uow_factory() as uow:
            pending = uow.scan_audit.find_pending_clean_receipt(stable_key)
            uow.commit()
        if pending is None:
            return None
        attempt, receipt = pending
        return _CleanScanIntention(attempt=attempt, pending=receipt)

    def _reserve(self, request: MediaReservationRequest) -> MediaLease:
        with self._uow_factory() as uow:
            try:
                lease = uow.media.reserve(request)
                uow.commit()
                return lease
            except Exception as exc:
                if type(exc).__name__ in {"IntegrityError", "MediaLeaseStateError"}:
                    raise MediaReferencePendingError(
                        "media reservation is pending; retry with idempotency key"
                    ) from exc
                raise

    def _finalize(self, request: FinalizeMediaRequest) -> FinalizeMediaOutcome:
        with self._uow_factory() as uow:
            try:
                outcome = uow.media.finalize(request)
                uow.commit()
                return outcome
            except Exception as exc:
                if type(exc).__name__ in {"IntegrityError", "MediaLeaseStateError"}:
                    raise MediaReferencePendingError(
                        "media finalization is pending; retry with idempotency key"
                    ) from exc
                raise

    def _confirm_reference(self, intent: MediaReferenceIntent) -> IngestedEvidenceResult:
        with self._uow_factory() as uow:
            result = uow.media.confirm_reference(intent)
            uow.commit()
            return result

    def _find_completed_reference_result(
        self,
        expected: FinalizeMediaOutcome,
        request: MediaReservationRequest,
    ) -> IngestedEvidenceResult | None:
        replay = self._find_replay(request)
        if replay is None or not self._same_completed_reference_fact(expected, replay):
            return None
        return replay.result

    @staticmethod
    def _same_completed_reference_fact(
        expected: FinalizeMediaOutcome,
        replay: FinalizeMediaOutcome,
    ) -> bool:
        expected_intent = expected.reference_intent
        replay_intent = replay.reference_intent
        return (
            expected_intent.action == "MARK_REFERENCED"
            and replay.evidence_visible
            and replay_intent.action == "NOOP"
            and replay_intent.staged == expected_intent.staged
            and replay.result == expected.result
            and replay.result.media_item_id == expected_intent.staged.media_item_id
        )

    @staticmethod
    def _request_from_lease(lease: MediaLease) -> MediaReservationRequest:
        return MediaReservationRequest(
            owner_id=lease.owner_id,
            session_id=lease.session_id,
            idempotency_key=lease.idempotency_key,
            fingerprint=lease.fingerprint,
        )

    def _reject(self, lease: MediaLease, reason: str) -> None:
        with self._uow_factory() as uow:
            uow.media.reject(lease, reason)
            uow.commit()

    def _complete_replay_reference_intent(
        self,
        outcome: FinalizeMediaOutcome,
        cleanup_errors: list[BaseException],
        replay_request: MediaReservationRequest | None = None,
    ) -> IngestedEvidenceResult:
        self._assert_replay_reference_outcome(outcome)
        intent = outcome.reference_intent
        if intent.action == "MARK_REFERENCED":
            try:
                self._object_store.mark_referenced(intent.staged)
                return self._confirm_reference(intent)
            except Exception as exc:
                if replay_request is not None:
                    completed = self._find_completed_reference_result(outcome, replay_request)
                    if completed is not None:
                        return completed
                raise PostCommitReferenceError(
                    "media reference completion needs reconciliation"
                ) from exc
        if intent.action == "ABORT_STAGED":
            self._run_cleanup(
                cleanup_errors, lambda: self._object_store.abort_staged(intent.staged)
            )
            if not outcome.evidence_visible:
                raise MediaReferencePendingError(
                    "media reference is pending; retry with idempotency key"
                )
        return outcome.result

    def _complete_fresh_reference_intent(
        self,
        outcome: FinalizeMediaOutcome,
        staged: StagedMediaObject,
        lease: MediaLease,
        cleanup_errors: list[BaseException],
    ) -> IngestedEvidenceResult:
        self._assert_fresh_reference_outcome(outcome, staged)
        intent = outcome.reference_intent
        if intent.action == "MARK_REFERENCED":
            try:
                self._object_store.mark_referenced(intent.staged)
                return self._confirm_reference(intent)
            except Exception as exc:
                completed = self._find_completed_reference_result(
                    outcome,
                    self._request_from_lease(lease),
                )
                if completed is not None:
                    return completed
                raise PostCommitReferenceError(
                    "media reference completion needs reconciliation"
                ) from exc
        if intent.action == "ABORT_STAGED":
            self._run_cleanup(cleanup_errors, lambda: self._object_store.abort_staged(staged))
            if not outcome.evidence_visible:
                return self._reconcile_pending_reference(lease, cleanup_errors)
            return outcome.result
        if intent.action == "NOOP":
            self._run_cleanup(cleanup_errors, lambda: self._object_store.abort_staged(staged))
            if not outcome.evidence_visible:
                raise MediaReferencePendingError(
                    "media reference is pending; retry with idempotency key"
                )
            return outcome.result
        raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")

    def _reconcile_pending_reference(
        self,
        lease: MediaLease,
        cleanup_errors: list[BaseException],
    ) -> IngestedEvidenceResult:
        replay_request = self._request_from_lease(lease)
        replay = self._find_replay(replay_request)
        if replay is not None:
            if replay.reference_intent.action == "MARK_REFERENCED":
                try:
                    return self._complete_replay_reference_intent(
                        replay,
                        cleanup_errors,
                        replay_request,
                    )
                except PostCommitReferenceError as exc:
                    raise MediaReferencePendingError(
                        "media reference is pending; retry with idempotency key"
                    ) from exc
            if replay.evidence_visible:
                return replay.result
        raise MediaReferencePendingError("media reference is pending; retry with idempotency key")

    def _assert_replay_reference_outcome(self, outcome: FinalizeMediaOutcome) -> None:
        action = outcome.reference_intent.action
        if action == "MARK_REFERENCED":
            if outcome.evidence_visible:
                raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")
            if outcome.reference_intent.staged.media_item_id != outcome.result.media_item_id:
                raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")
            return
        if action == "ABORT_STAGED":
            if outcome.result.media_item_id == outcome.reference_intent.staged.media_item_id:
                raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")
            return
        if action == "NOOP":
            if not outcome.evidence_visible:
                raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")
            return
        raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")

    def _assert_fresh_reference_outcome(
        self,
        outcome: FinalizeMediaOutcome,
        staged: StagedMediaObject,
    ) -> None:
        action = outcome.reference_intent.action
        if action == "MARK_REFERENCED" and outcome.reference_intent.staged != staged:
            raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")
        if action == "MARK_REFERENCED":
            if outcome.evidence_visible or outcome.result.media_item_id != staged.media_item_id:
                raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")
            return
        if action == "ABORT_STAGED":
            if outcome.evidence_visible:
                if (
                    outcome.reference_intent.staged.media_item_id != staged.media_item_id
                    or outcome.reference_intent.staged.reservation_id != staged.reservation_id
                ):
                    raise InvalidMediaReferenceOutcomeError(
                        "media reference outcome is inconsistent"
                    )
                return
            if outcome.reference_intent.staged != staged:
                raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")
            return
        if action == "NOOP":
            if (
                not outcome.evidence_visible
                or outcome.result.media_item_id != outcome.reference_intent.staged.media_item_id
            ):
                raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")
            return
        raise InvalidMediaReferenceOutcomeError("media reference outcome is inconsistent")

    def _assert_quarantine_facts(
        self,
        quarantine: ReadOnlyQuarantineObject,
        byte_size: int,
        source_sha256: str,
    ) -> None:
        if quarantine.byte_size != byte_size or quarantine.streaming_sha256 != source_sha256:
            raise QuarantineMetadataMismatchError(
                "quarantine metadata does not match streamed media"
            )

    def _scan_and_record(
        self,
        lease: MediaLease,
        quarantine: ReadOnlyQuarantineObject,
        source_sha256: str,
        byte_size: int,
        declared_media_type: str | None,
    ) -> _CleanScanIntention:
        started_at = datetime.now(UTC)
        snapshot = self._malware_scanner.audit_snapshot
        try:
            with quarantine.open() as stream:
                verdict = self._malware_scanner.scan(
                    ReadOnlyMediaSource(
                        stream=stream,
                        byte_size=byte_size,
                        streaming_sha256=source_sha256,
                    )
                )
        except Exception:
            verdict = MalwareScanVerdict(status="error", engine=snapshot.scanner_backend)
            scanner_exception_detail: str | None = "scanner exception before verdict"
        else:
            scanner_exception_detail = None
        finished_at = datetime.now(UTC)
        result = (
            ScanResultKind.ERROR if verdict.status == "unknown" else ScanResultKind(verdict.status)
        )
        rejection_code = getattr(verdict, "rejection_code", None)
        if result is not ScanResultKind.CLEAN and rejection_code is None:
            rejection_code = default_scan_rejection_code(result)
        stable_key = self._scan_idempotency_key(
            MediaReservationRequest(
                owner_id=lease.owner_id,
                session_id=lease.session_id,
                idempotency_key=lease.idempotency_key,
                fingerprint=lease.fingerprint,
            )
        )
        attempt = MediaScanAttempt(
            attempt_id=str(uuid5(NAMESPACE_URL, f"focusproof:scan:{stable_key}")),
            artifact_sha256=source_sha256,
            content_type=declared_media_type or "application/octet-stream",
            scanner_backend=snapshot.scanner_backend,
            definitions_version=snapshot.definitions_version,
            definitions_fresh_at=snapshot.definitions_fresh_at,
            definitions_age_seconds=snapshot.definitions_age_seconds,
            max_bytes=snapshot.max_bytes,
            max_concurrent_scans=snapshot.max_concurrent_scans,
            deadline_ms=snapshot.deadline_ms,
            socket_timeout_ms=snapshot.socket_timeout_ms,
            scan_result=result,
            rejection_code=rejection_code,
            rejection_detail=(
                scanner_exception_detail
                or (
                    "legacy unknown scan result"
                    if verdict.status == "unknown"
                    else getattr(verdict, "rejection_detail", None)
                )
            ),
            started_at=started_at,
            finished_at=finished_at,
            idempotency_key=stable_key,
        )
        try:
            with self._uow_factory() as uow:
                attempt = uow.scan_audit.record_attempt(attempt)
                if result is ScanResultKind.CLEAN:
                    receipt_hash = sha256(
                        json.dumps(
                            {"attempt_id": attempt.attempt_id, "sha256": source_sha256},
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                    pending = self._quarantine_store.pending_clean_receipt(
                        quarantine,
                        receipt_id=str(uuid5(NAMESPACE_URL, f"focusproof:receipt:{stable_key}")),
                        attempt_id=attempt.attempt_id,
                        artifact_sha256=source_sha256,
                        receipt_hash=receipt_hash,
                        created_at=finished_at,
                    )
                    pending = uow.scan_audit.record_pending_clean_receipt(pending)
                else:
                    uow.media.reject(lease, self._scan_error_code(result))
                uow.commit()
        except Exception as exc:
            if result is ScanResultKind.CLEAN and type(exc).__name__ == "IntegrityError":
                raise MediaPromotionPendingError(
                    "clean scan persistence is pending; retry with idempotency key"
                ) from exc
            if result is not ScanResultKind.CLEAN:
                exc.scan_rejection_committed = True  # type: ignore[attr-defined]
            raise
        if result is not ScanResultKind.CLEAN:
            code = self._scan_error_code(result)
            error = _SCAN_ERRORS[code](
                {
                    "media_malware_detected": "malware detected",
                    "media_scan_unavailable": "media scan unavailable",
                    "media_scan_timeout": "media scan timeout",
                    "media_scan_failed": "media scan failed",
                }[code]
            )
            error.scan_rejection_committed = True  # type: ignore[attr-defined]
            raise error
        if verdict.status == "unknown":
            raise MalwareScanFailedError("media scan failed")
        self._accept_scan_verdict(verdict)
        return _CleanScanIntention(attempt=attempt, pending=pending)

    @staticmethod
    def _scan_idempotency_key(request: MediaReservationRequest) -> str:
        return f"{request.session_id}:{request.idempotency_key}:{request.fingerprint}"

    def _promote_pending_clean_receipt(
        self,
        intention: _CleanScanIntention,
        spool: ReadOnlyQuarantineObject | None,
    ) -> ReadOnlyQuarantineObject:
        owner_token = f"publication-{uuid4().hex}"
        claim = self._claim_pending_publication(intention.pending.receipt_id, owner_token)
        if claim is None:
            raise MediaPromotionPendingError(
                "clean media publication is pending; retry with idempotency key"
            )
        intention = _CleanScanIntention(attempt=intention.attempt, pending=claim.pending)
        if not claim.acquired:
            if claim.pending.publication_status == "published":
                promoted = self._quarantine_store.open_formal_clean_receipt(claim.pending)
                if promoted is not None:
                    return promoted
                raise ValueError("published formal quarantine artifact is missing")
            raise MediaPromotionPendingError(
                "clean media publication is in progress; retry with idempotency key"
            )
        try:
            now = datetime.now(UTC)
            with self._uow_factory() as uow:
                locked = uow.scan_audit.refresh_pending_clean_publication_lease(
                    intention.pending.receipt_id,
                    owner_token=owner_token,
                    now=now,
                    lease_expires_at=now + timedelta(seconds=_PUBLICATION_LEASE_SECONDS),
                )
                uow.commit()
            if locked is None:
                raise MediaPromotionPendingError(
                    "clean media publication owner changed; retry with idempotency key"
                )
            quarantine = self._quarantine_store.open_formal_clean_receipt(locked)
            if quarantine is None:
                source = spool or self._quarantine_store.open_pending_spool(locked)
                quarantine = self._quarantine_store.promote_clean_spool(
                    source,
                    receipt_id=locked.receipt_id,
                    receipt_hash=locked.receipt_hash,
                    formal_artifact_id=locked.formal_artifact_id,
                    quarantine_expires_at=locked.quarantine_expires_at,
                )
            with self._uow_factory() as uow:
                uow.scan_audit.mark_pending_clean_publication_published(
                    locked.receipt_id,
                    owner_token=owner_token,
                    formal_artifact_id=quarantine.quarantine_id,
                    now=datetime.now(UTC),
                )
                uow.commit()
            return quarantine
        except ValueError:
            self._mark_publication_failed(intention.pending.receipt_id, owner_token, "ValueError")
            raise
        except Exception as exc:
            self._mark_publication_failed(
                intention.pending.receipt_id, owner_token, type(exc).__name__
            )
            raise MediaPromotionPendingError(
                "clean media promotion is pending; retry with idempotency key"
            ) from exc

    def _claim_pending_publication(
        self,
        receipt_id: str,
        owner_token: str,
    ) -> CleanReceiptPublicationClaim | None:
        now = datetime.now(UTC)
        with self._uow_factory() as uow:
            claim = uow.scan_audit.claim_pending_clean_publication(
                receipt_id,
                owner_token=owner_token,
                now=now,
                lease_expires_at=now + timedelta(seconds=_PUBLICATION_LEASE_SECONDS),
            )
            uow.commit()
            return claim

    def _mark_publication_failed(
        self,
        receipt_id: str,
        owner_token: str,
        reason: str,
    ) -> None:
        try:
            with self._uow_factory() as uow:
                uow.scan_audit.mark_pending_clean_publication_failed(
                    receipt_id,
                    owner_token=owner_token,
                    now=datetime.now(UTC),
                    reason=reason,
                )
                uow.commit()
        except Exception:
            return None

    def _activate_clean_receipt(
        self,
        intention: _CleanScanIntention,
        quarantine: ReadOnlyQuarantineObject,
    ) -> None:
        if (
            quarantine.receipt_id != intention.pending.receipt_id
            or quarantine.receipt_hash != intention.pending.receipt_hash
            or quarantine.streaming_sha256 != intention.attempt.artifact_sha256
        ):
            raise QuarantineMetadataMismatchError("quarantine receipt binding mismatch")
        with self._uow_factory() as uow:
            uow.scan_audit.record_clean_receipt(
                MediaCleanReceipt.from_attempt(
                    intention.attempt,
                    receipt_id=intention.pending.receipt_id,
                    receipt_hash=intention.pending.receipt_hash,
                    quarantine_path=quarantine.quarantine_id,
                    quarantine_expires_at=quarantine.quarantine_expires_at,
                    created_at=datetime.now(UTC),
                )
            )
            uow.commit()

    @staticmethod
    def _scan_error_code(result: ScanResultKind) -> str:
        return {
            ScanResultKind.MALICIOUS: "media_malware_detected",
            ScanResultKind.OVERSIZE: "media_scan_failed",
            ScanResultKind.TIMEOUT: "media_scan_timeout",
            ScanResultKind.UNAVAILABLE: "media_scan_unavailable",
            ScanResultKind.ERROR: "media_scan_failed",
        }[result]

    def _accept_scan_verdict(self, verdict: MalwareScanVerdict) -> None:
        code = malware_rejection_code(verdict)
        if code is None:
            return
        error_type = _SCAN_ERRORS[code]
        messages = {
            "media_malware_detected": "malware detected",
            "media_scan_unavailable": "media scan unavailable",
            "media_scan_timeout": "media scan timeout",
            "media_scan_failed": "media scan failed",
            "media_scan_unknown": "media scan unknown",
        }
        raise error_type(messages[code])

    def _validate(
        self,
        quarantine: ReadOnlyQuarantineObject,
        source_sha256: str,
        byte_size: int,
        declared_media_type: str | None,
    ) -> ValidatedMediaMetadata:
        with quarantine.open() as stream:
            metadata = self._validator.validate(
                ReadOnlyMediaSource(
                    stream=stream,
                    byte_size=byte_size,
                    streaming_sha256=source_sha256,
                ),
                declared_media_type,
            )
        if metadata.byte_size != byte_size or metadata.source_sha256 != source_sha256:
            raise ValidatedMetadataMismatchError("validated metadata does not match source media")
        return ValidatedMediaMetadata(
            media_type=metadata.media_type,
            byte_size=metadata.byte_size,
            source_sha256=metadata.source_sha256,
            attributes=freeze_attributes(metadata.attributes),
        )

    def _normalize(
        self,
        quarantine: ReadOnlyQuarantineObject,
        metadata: ValidatedMediaMetadata,
    ) -> NormalizedMediaSource:
        with quarantine.open() as stream:
            return self._normalizer.normalize(
                ReadOnlyMediaSource(
                    stream=stream,
                    byte_size=metadata.byte_size,
                    streaming_sha256=metadata.source_sha256,
                ),
                metadata,
            )

    def _verify_normalized(self, normalized: NormalizedMediaSource) -> tuple[str, int]:
        digest = sha256()
        byte_size = 0
        for chunk in iter(lambda: normalized.stream.read(1024 * 1024), b""):
            byte_size += len(chunk)
            digest.update(chunk)
        normalized_sha256 = digest.hexdigest()
        if normalized.normalized_sha256 != normalized_sha256 or normalized.byte_size != byte_size:
            raise ValueError("normalized media source metadata mismatch")
        return normalized_sha256, byte_size

    def _run_cleanup(
        self,
        cleanup_errors: list[BaseException],
        action: Callable[[], object],
    ) -> bool:
        try:
            action()
            return True
        except Exception as exc:
            cleanup_errors.append(exc)
            return False

    def _publish_cleanup_errors(self, cleanup_errors: list[BaseException]) -> None:
        if cleanup_errors:
            self._cleanup_error_reporter(CleanupError(cleanup_errors))

    def _attach_cleanup_notes(
        self,
        primary: BaseException,
        cleanup_errors: list[BaseException],
    ) -> None:
        for cleanup_error in cleanup_errors:
            primary.add_note(f"media cleanup failed: {type(cleanup_error).__name__}")
