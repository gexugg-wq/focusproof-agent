from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CursorResult, delete, func, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from focusproof.persistence.models import (
    AuditEventModel,
    EvidenceModel,
    LearnerAnswerModel,
    LearningSessionModel,
    ReviewModel,
    SecurityAuditEventModel,
    VerifiedPrincipalModel,
    MediaArtifactModel,
    MediaCleanReceiptModel,
    MediaIngestionReservationModel,
    MediaScanAttemptModel,
    SpeechResourceSlotModel,
    SpeechTranscriptionRequestModel,
)
from focusproof.runtime.security_audit import (
    SecurityAuditOutcome,
    SecurityAuditReasonCategory,
)
from focusproof.speech_core.errors import (
    SpeechAdmissionError,
    SpeechErrorCode,
)
from focusproof.speech_core.models import TranscriptionState


if TYPE_CHECKING:
    from focusproof.media_core.models import (
        FinalizeMediaOutcome,
        FinalizeMediaRequest,
        IngestedEvidenceResult,
        MediaLease,
        MediaReferenceIntent,
        MediaReservationRequest,
        StagedMediaObject,
    )


class IdempotencyConflictError(ValueError):
    pass


class MediaQuotaExceededError(ValueError):
    pass


class MediaAuthorizationError(ValueError):
    pass


class MediaLeaseStateError(ValueError):
    pass


class SqlMediaTransactionRepository:
    def __init__(
        self,
        session: Session,
        *,
        max_items: int = 4,
        max_distinct_bytes: int = 20 * 1024 * 1024,
        lease_seconds: int = 900,
    ) -> None:
        self._session = session
        self._max_items = max_items
        self._max_distinct_bytes = max_distinct_bytes
        self._lease_seconds = lease_seconds

    def reserve(self, request: MediaReservationRequest) -> MediaLease:
        self._lock_owned_session(request.owner_id, request.session_id)
        now = datetime.now(UTC)
        expired = self._session.scalars(
            select(MediaIngestionReservationModel).where(
                MediaIngestionReservationModel.owner_id == request.owner_id,
                MediaIngestionReservationModel.session_id == request.session_id,
                MediaIngestionReservationModel.status == "ACTIVE",
                MediaIngestionReservationModel.expires_at <= now,
            )
        )
        for reservation in expired:
            self._transition_reservation(reservation, "EXPIRED", now)
        existing = self._find_reservation(
            request.owner_id, request.session_id, request.idempotency_key
        )
        if existing is not None:
            self._check_fingerprint(existing, request.fingerprint)
            if existing.status in {"ACTIVE", "PENDING_REFERENCE"}:
                return self._lease(existing)
            raise MediaLeaseStateError("idempotency key is terminal")
        visible = self._evidence_count(request.session_id)
        occupied = list(
            self._session.scalars(
                select(MediaIngestionReservationModel).where(
                    MediaIngestionReservationModel.session_id == request.session_id,
                    MediaIngestionReservationModel.status.in_(("ACTIVE", "PENDING_REFERENCE")),
                )
            )
        )
        if visible + len(occupied) + 1 > self._max_items:
            raise MediaQuotaExceededError("media item quota exceeded")
        used_slots = {item.slot for item in occupied}
        slot = next(index for index in range(self._max_items) if index not in used_slots)
        reservation = MediaIngestionReservationModel(
            reservation_id=f"res_{uuid4().hex}",
            media_item_id=f"media_{uuid4().hex}",
            owner_id=request.owner_id,
            session_id=request.session_id,
            idempotency_key=request.idempotency_key,
            fingerprint=request.fingerprint,
            slot=slot,
            status="ACTIVE",
            active=True,
            expires_at=now + timedelta(seconds=self._lease_seconds),
            created_at=now,
            updated_at=now,
        )
        try:
            with self._session.begin_nested():
                self._session.add(reservation)
                self._session.flush()
            return self._lease(reservation)
        except IntegrityError:
            existing = self._find_reservation(
                request.owner_id,
                request.session_id,
                request.idempotency_key,
            )
            if existing is not None:
                self._check_fingerprint(existing, request.fingerprint)
                if existing.status in {"ACTIVE", "PENDING_REFERENCE"}:
                    return self._lease(existing)
            raise

    def find_idempotent_outcome(
        self,
        owner_id: str,
        session_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> FinalizeMediaOutcome | None:
        self._lock_owned_session(owner_id, session_id)
        reservation = self._find_reservation(owner_id, session_id, idempotency_key)
        if reservation is None:
            return None
        self._check_fingerprint(reservation, fingerprint)
        return self._replay_locked_reservation(reservation)

    def _replay_locked_reservation(
        self,
        reservation: MediaIngestionReservationModel,
    ) -> FinalizeMediaOutcome | None:
        if reservation.status == "PENDING_REFERENCE":
            artifact = self._require_artifact(reservation)
            if reservation.intent_action == "ABORT_STAGED" and artifact.state == "REFERENCED":
                self._complete_reservation(reservation, artifact, mode="FOLLOWER")
                return self._outcome(reservation, "NOOP", True)
            return self._outcome(reservation)
        if reservation.status == "COMPLETED":
            action = "ABORT_STAGED" if reservation.completion_mode == "DIRECT_REUSE" else "NOOP"
            return self._outcome(reservation, action, True)
        return None

    def finalize(self, request: FinalizeMediaRequest) -> FinalizeMediaOutcome:
        self._lock_owned_session(request.lease.owner_id, request.lease.session_id)
        reservation = self._session.scalar(
            select(MediaIngestionReservationModel)
            .where(MediaIngestionReservationModel.reservation_id == request.lease.reservation_id)
            .with_for_update()
        )
        if reservation is None:
            raise MediaLeaseStateError("media lease is not active")
        self._check_lease(reservation, request.lease)
        self._check_staged_identity(reservation, request)
        if reservation.status != "ACTIVE":
            if reservation.status in {"PENDING_REFERENCE", "COMPLETED"}:
                outcome = self._replay_locked_reservation(reservation)
                if outcome is not None:
                    if (
                        reservation.status == "PENDING_REFERENCE"
                        and outcome.reference_intent.action == "MARK_REFERENCED"
                    ):
                        from focusproof.media_core.models import (
                            FinalizeMediaOutcome,
                            MediaReferenceIntent,
                            StagedMediaObject,
                        )

                        return FinalizeMediaOutcome(
                            result=outcome.result,
                            reference_intent=MediaReferenceIntent(
                                staged=StagedMediaObject(
                                    media_item_id=request.staged_media_item_id,
                                    reservation_id=request.lease.reservation_id,
                                    opaque_object_key=request.opaque_object_key,
                                    manifest_id=request.manifest_id,
                                ),
                                action="ABORT_STAGED",
                            ),
                            evidence_visible=False,
                        )
                    return outcome
            raise MediaLeaseStateError("media lease is not active")
        if self._is_expired(reservation.expires_at):
            raise MediaLeaseStateError("media lease expired")
        if (
            self._evidence_count(reservation.session_id)
            + self._pending_count(reservation.session_id)
            > self._max_items
        ):
            raise MediaQuotaExceededError("media item quota exceeded")

        artifact = self._find_artifact(reservation.owner_id, request.normalized_sha256)
        if artifact is not None:
            self._check_canonical(artifact, request)
            self._check_distinct_quota(
                reservation.session_id, artifact, request.normalized_byte_size
            )
            self._store_pending_facts(reservation, artifact, request)
            if artifact.state == "REFERENCED":
                reservation.intent_action = "ABORT_STAGED"
                self._complete_reservation(reservation, artifact, mode="DIRECT_REUSE")
                return self._outcome(reservation, "ABORT_STAGED", True)
            reservation.intent_action = "ABORT_STAGED"
            self._transition_reservation(reservation, "PENDING_REFERENCE")
            self._session.flush()
            return self._outcome(reservation)

        self._check_distinct_quota(reservation.session_id, None, request.normalized_byte_size)
        now = datetime.now(UTC)
        candidate = MediaArtifactModel(
            media_item_id=request.staged_media_item_id,
            owner_id=reservation.owner_id,
            creator_reservation_id=reservation.reservation_id,
            opaque_object_key=request.opaque_object_key,
            manifest_id=request.manifest_id,
            media_type=request.media_type,
            normalized_sha256=request.normalized_sha256,
            normalized_byte_size=request.normalized_byte_size,
            state="PENDING_REFERENCE",
            created_at=now,
        )
        try:
            with self._session.begin_nested():
                self._session.add(candidate)
                self._session.flush()
            artifact = candidate
            action = "MARK_REFERENCED"
        except IntegrityError:
            artifact = self._find_artifact(reservation.owner_id, request.normalized_sha256)
            if artifact is None:
                raise
            self._check_canonical(artifact, request)
            action = "ABORT_STAGED"
        self._store_pending_facts(reservation, artifact, request)
        if artifact.state == "REFERENCED":
            reservation.intent_action = "ABORT_STAGED"
            self._complete_reservation(reservation, artifact, mode="DIRECT_REUSE")
            return self._outcome(reservation, "ABORT_STAGED", True)
        reservation.intent_action = action
        self._transition_reservation(reservation, "PENDING_REFERENCE", now)
        self._session.flush()
        return self._outcome(reservation)

    def confirm_reference(self, intent: MediaReferenceIntent) -> IngestedEvidenceResult:
        identity = self._session.execute(
            select(
                MediaIngestionReservationModel.owner_id,
                MediaIngestionReservationModel.session_id,
            ).where(
                MediaIngestionReservationModel.reservation_id == intent.staged.reservation_id
            )
        ).one_or_none()
        if identity is None:
            raise MediaLeaseStateError("media reference intent is unavailable")
        owner_id, session_id = identity
        self._lock_owned_session(owner_id, session_id)
        reservation = self._session.scalar(
            select(MediaIngestionReservationModel)
            .where(MediaIngestionReservationModel.reservation_id == intent.staged.reservation_id)
            .with_for_update()
        )
        if reservation is None:
            raise MediaLeaseStateError("media reference intent is unavailable")
        if reservation.owner_id != owner_id or reservation.session_id != session_id:
            raise MediaLeaseStateError("media reference intent identity changed")
        if (
            reservation.intent_action != "MARK_REFERENCED"
            or self._staged(reservation) != intent.staged
            or intent.action != "MARK_REFERENCED"
        ):
            raise MediaLeaseStateError("media reference intent does not match")
        if reservation.status == "COMPLETED":
            return self._result(reservation.result_json)
        if reservation.status != "PENDING_REFERENCE":
            raise MediaLeaseStateError("media reference intent is not pending")
        artifact = self._session.scalar(
            select(MediaArtifactModel)
            .where(MediaArtifactModel.media_item_id == reservation.canonical_artifact_id)
            .with_for_update()
        )
        if artifact is None or artifact.state not in {
            "PENDING_REFERENCE",
            "REFERENCED",
        }:
            raise MediaLeaseStateError("media artifact is unavailable")
        self._transition_artifact(artifact, "REFERENCED")
        self._complete_reservation(reservation, artifact, mode="ADOPTED")
        self._session.flush()
        return self._result(reservation.result_json)

    def list_pending_reference_outcomes(self, limit: int) -> tuple[FinalizeMediaOutcome, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("pending reference limit must be between 1 and 100")
        reservations = self._session.scalars(
            select(MediaIngestionReservationModel)
            .where(
                MediaIngestionReservationModel.status == "PENDING_REFERENCE",
                MediaIngestionReservationModel.intent_action == "MARK_REFERENCED",
            )
            .order_by(
                MediaIngestionReservationModel.created_at,
                MediaIngestionReservationModel.reservation_id,
            )
            .limit(limit)
        )
        return tuple(self._outcome(item) for item in reservations)

    def reject(self, lease: MediaLease, reason: str) -> None:
        self._lock_owned_session(lease.owner_id, lease.session_id)
        reservation = self._session.scalar(
            select(MediaIngestionReservationModel)
            .where(MediaIngestionReservationModel.reservation_id == lease.reservation_id)
            .with_for_update()
        )
        if reservation is None:
            return
        self._check_lease(reservation, lease)
        if reservation.status != "ACTIVE":
            return
        now = datetime.now(UTC)
        status = "EXPIRED" if self._is_expired(reservation.expires_at) else "REJECTED"
        self._transition_reservation(reservation, status, now)
        reservation.rejection_reason = reason[:128]

    def _complete_reservation(
        self,
        reservation: MediaIngestionReservationModel,
        artifact: MediaArtifactModel,
        *,
        mode: str,
    ) -> None:
        if reservation.status == "COMPLETED":
            return
        self._transition_reservation(reservation, "COMPLETED")
        reservation.completion_mode = mode
        evidence = self._session.get(EvidenceModel, reservation.evidence_id)
        if evidence is None:
            result = self._result(reservation.result_json)
            self._session.add(
                EvidenceModel(
                    evidence_id=result.evidence_id,
                    session_id=reservation.session_id,
                    evidence_type=result.media_type,
                    content_hash=result.normalized_sha256,
                    text_content=result.learner_explanation,
                    source_url=None,
                    metadata_json=dict(result.attributes),
                    artifact_id=artifact.media_item_id,
                    created_at=datetime.now(UTC),
                )
            )
        self._session.flush()

    @staticmethod
    def _transition_reservation(
        reservation: MediaIngestionReservationModel,
        status: str,
        now: datetime | None = None,
    ) -> None:
        allowed = {
            "ACTIVE": {"PENDING_REFERENCE", "COMPLETED", "REJECTED", "EXPIRED"},
            "PENDING_REFERENCE": {"COMPLETED"},
        }
        if status not in allowed.get(reservation.status, set()):
            raise MediaLeaseStateError("invalid media reservation transition")
        reservation.status = status
        reservation.active = True if status == "PENDING_REFERENCE" else None
        reservation.updated_at = now or datetime.now(UTC)

    @staticmethod
    def _transition_artifact(artifact: MediaArtifactModel, state: str) -> None:
        if artifact.state == state:
            return
        if artifact.state != "PENDING_REFERENCE" or state != "REFERENCED":
            raise MediaLeaseStateError("invalid media artifact transition")
        artifact.state = state

    def _store_pending_facts(
        self,
        reservation: MediaIngestionReservationModel,
        artifact: MediaArtifactModel,
        request: FinalizeMediaRequest,
    ) -> None:
        from focusproof.media_core.models import IngestedEvidenceResult

        evidence_id = reservation.evidence_id or f"ev_{uuid4().hex}"
        result = IngestedEvidenceResult(
            evidence_id=evidence_id,
            media_item_id=artifact.media_item_id,
            artifact_ref=f"focusproof-artifact://{artifact.media_item_id}",
            media_type=artifact.media_type,
            normalized_sha256=artifact.normalized_sha256,
            byte_size=artifact.normalized_byte_size,
            learner_explanation=request.learner_explanation,
            attributes=request.attributes,
        )
        reservation.canonical_artifact_id = artifact.media_item_id
        reservation.evidence_id = evidence_id
        reservation.staged_object_key = request.opaque_object_key
        reservation.staged_manifest_id = request.manifest_id
        reservation.media_type = request.media_type
        reservation.normalized_sha256 = request.normalized_sha256
        reservation.normalized_byte_size = request.normalized_byte_size
        reservation.learner_explanation = request.learner_explanation
        reservation.attributes_json = dict(request.attributes)
        reservation.result_json = self._result_json(result)

    def _check_distinct_quota(
        self,
        session_id: str,
        artifact: MediaArtifactModel | None,
        new_size: int,
    ) -> None:
        artifact_ids = set(
            self._session.scalars(
                select(EvidenceModel.artifact_id).where(
                    EvidenceModel.session_id == session_id,
                    EvidenceModel.artifact_id.is_not(None),
                )
            )
        )
        artifact_ids.update(
            item
            for item in self._session.scalars(
                select(MediaIngestionReservationModel.canonical_artifact_id).where(
                    MediaIngestionReservationModel.session_id == session_id,
                    MediaIngestionReservationModel.status == "PENDING_REFERENCE",
                    MediaIngestionReservationModel.canonical_artifact_id.is_not(None),
                )
            )
            if item is not None
        )
        used = 0
        if artifact_ids:
            used = int(
                self._session.scalar(
                    select(
                        func.coalesce(func.sum(MediaArtifactModel.normalized_byte_size), 0)
                    ).where(MediaArtifactModel.media_item_id.in_(artifact_ids))
                )
                or 0
            )
        candidate_id = artifact.media_item_id if artifact is not None else None
        delta = (
            0
            if candidate_id in artifact_ids
            else (artifact.normalized_byte_size if artifact is not None else new_size)
        )
        if used + delta > self._max_distinct_bytes:
            raise MediaQuotaExceededError("distinct media byte quota exceeded")

    def _outcome(
        self,
        reservation: MediaIngestionReservationModel,
        action: str | None = None,
        visible: bool | None = None,
    ) -> FinalizeMediaOutcome:
        from focusproof.media_core.models import FinalizeMediaOutcome, MediaReferenceIntent

        actual_action = cast(Any, action or reservation.intent_action)
        if actual_action not in {"MARK_REFERENCED", "ABORT_STAGED", "NOOP"}:
            raise MediaLeaseStateError("media reference intent is unavailable")
        return FinalizeMediaOutcome(
            result=self._result(reservation.result_json),
            reference_intent=MediaReferenceIntent(
                staged=self._staged(reservation),
                action=actual_action,
            ),
            evidence_visible=(reservation.status == "COMPLETED" if visible is None else visible),
        )

    @staticmethod
    def _staged(
        reservation: MediaIngestionReservationModel,
    ) -> StagedMediaObject:
        from focusproof.media_core.models import StagedMediaObject

        if reservation.staged_object_key is None or reservation.staged_manifest_id is None:
            raise MediaLeaseStateError("staged media intent is unavailable")
        return StagedMediaObject(
            media_item_id=reservation.media_item_id,
            reservation_id=reservation.reservation_id,
            opaque_object_key=reservation.staged_object_key,
            manifest_id=reservation.staged_manifest_id,
        )

    def _find_reservation(
        self, owner_id: str, session_id: str, idempotency_key: str
    ) -> MediaIngestionReservationModel | None:
        return self._session.scalar(
            select(MediaIngestionReservationModel)
            .where(
                MediaIngestionReservationModel.owner_id == owner_id,
                MediaIngestionReservationModel.session_id == session_id,
                MediaIngestionReservationModel.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )

    def _find_artifact(self, owner_id: str, normalized_sha256: str) -> MediaArtifactModel | None:
        return self._session.scalar(
            select(MediaArtifactModel).where(
                MediaArtifactModel.owner_id == owner_id,
                MediaArtifactModel.normalized_sha256 == normalized_sha256,
            ).with_for_update()
        )

    def _require_artifact(self, reservation: MediaIngestionReservationModel) -> MediaArtifactModel:
        artifact = self._session.scalar(
            select(MediaArtifactModel)
            .where(MediaArtifactModel.media_item_id == reservation.canonical_artifact_id)
            .with_for_update()
        )
        if artifact is None:
            raise MediaLeaseStateError("canonical media artifact is unavailable")
        return artifact

    def _lock_owned_session(self, owner_id: str, session_id: str) -> LearningSessionModel:
        bind = self._session.get_bind()
        if bind.dialect.name == "sqlite":
            result = cast(
                CursorResult[Any],
                self._session.execute(
                    update(LearningSessionModel)
                    .where(LearningSessionModel.session_id == session_id)
                    .values(updated_at=LearningSessionModel.updated_at)
                ),
            )
            if result.rowcount != 1:
                raise MediaAuthorizationError("media session is unavailable")
        locked = _lock_learning_session(self._session, session_id)
        if locked is None or locked.owner_user_id != owner_id:
            raise MediaAuthorizationError("media session is unavailable")
        return locked

    def _evidence_count(self, session_id: str) -> int:
        return int(
            self._session.scalar(
                select(func.count(EvidenceModel.evidence_id)).where(
                    EvidenceModel.session_id == session_id,
                    EvidenceModel.artifact_id.is_not(None),
                )
            )
            or 0
        )

    def _pending_count(self, session_id: str) -> int:
        return int(
            self._session.scalar(
                select(func.count(MediaIngestionReservationModel.reservation_id)).where(
                    MediaIngestionReservationModel.session_id == session_id,
                    MediaIngestionReservationModel.status.in_(("ACTIVE", "PENDING_REFERENCE")),
                )
            )
            or 0
        )

    @staticmethod
    def _check_fingerprint(reservation: MediaIngestionReservationModel, fingerprint: str) -> None:
        if reservation.fingerprint != fingerprint:
            raise IdempotencyConflictError("idempotency fingerprint conflict")

    def _check_lease(self, reservation: MediaIngestionReservationModel, lease: MediaLease) -> None:
        if self._lease(reservation) != lease:
            raise MediaLeaseStateError("media lease does not match")

    @staticmethod
    def _check_staged_identity(
        reservation: MediaIngestionReservationModel,
        request: FinalizeMediaRequest,
    ) -> None:
        if not (
            request.staged_media_item_id
            == request.lease.media_item_id
            == reservation.media_item_id
        ):
            raise MediaLeaseStateError("staged media item does not match lease")

    @staticmethod
    def _check_canonical(artifact: MediaArtifactModel, request: FinalizeMediaRequest) -> None:
        if (
            artifact.media_type != request.media_type
            or artifact.normalized_byte_size != request.normalized_byte_size
        ):
            raise MediaLeaseStateError("canonical media facts conflict")

    @staticmethod
    def _is_expired(value: datetime) -> bool:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value <= datetime.now(UTC)

    @staticmethod
    def _lease(model: MediaIngestionReservationModel) -> MediaLease:
        from focusproof.media_core.models import MediaLease

        return MediaLease(
            model.reservation_id,
            model.media_item_id,
            model.owner_id,
            model.session_id,
            model.slot,
            model.idempotency_key,
            model.fingerprint,
        )

    @staticmethod
    def _result_json(result: IngestedEvidenceResult) -> dict[str, Any]:
        return {
            "evidence_id": result.evidence_id,
            "media_item_id": result.media_item_id,
            "artifact_ref": result.artifact_ref,
            "media_type": result.media_type,
            "normalized_sha256": result.normalized_sha256,
            "byte_size": result.byte_size,
            "learner_explanation": result.learner_explanation,
            "attributes": dict(result.attributes),
        }

    @staticmethod
    def _result(value: dict[str, Any] | None) -> IngestedEvidenceResult:
        from focusproof.media_core.models import IngestedEvidenceResult

        if value is None:
            raise MediaLeaseStateError("media result is unavailable")
        return IngestedEvidenceResult(**value)


class StoredModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class StoredSession(StoredModel):
    session_id: str
    owner_user_id: str
    status: str
    adapter_mode: str
    domain: str
    title: str
    goal: str
    expected_output: str | None
    planned_minutes: int | None
    conversation_id: str
    runtime_mode: str
    review_result: dict[str, Any] | None
    goal_conversation_synced_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class StoredEvidence(StoredModel):
    evidence_id: str
    session_id: str
    evidence_type: str
    content_hash: str
    text_content: str | None
    source_url: str | None
    metadata: dict[str, Any]
    conversation_synced_at: datetime | None
    created_at: datetime


class StoredAnswer(StoredModel):
    session_id: str
    question_id: str
    answer: str
    version: int
    conversation_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StoredAuditEvent(StoredModel):
    event_id: str
    session_id: str
    sequence: int
    type: str
    actor: str
    payload: dict[str, Any]
    source_openhands_event_id: str | None
    created_at: datetime


class StoredReview(StoredModel):
    review_id: str
    session_id: str
    conversation_id: str
    review_status: str
    score: int | None
    result: dict[str, Any] | None
    native_event_count: int
    source_openhands_event_id: str | None
    created_at: datetime


class StoredPrincipal(StoredModel):
    principal_id: str
    issuer: str
    subject: str
    active: bool
    created_at: datetime
    state_changed_at: datetime


class StoredSecurityAuditEvent(StoredModel):
    id: str = Field(default_factory=lambda: f"audit_{uuid4().hex}")
    request_id: str
    principal_id: str | None
    token_fingerprint: str | None
    outcome: SecurityAuditOutcome
    reason_category: SecurityAuditReasonCategory
    occurred_at: datetime


class MediaMessageArtifactFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    receipt_id: str
    attempt_id: str
    scan_result: str
    artifact_ref: str
    artifact_sha256: str
    opaque_object_key: str
    media_type: str
    normalized_sha256: str
    byte_size: int
    width: int
    height: int


class SessionRepository(Protocol):
    def create(self, record: StoredSession) -> StoredSession: ...
    def get(self, session_id: str) -> StoredSession | None: ...
    def get_owned(self, session_id: str, owner_user_id: str) -> StoredSession | None: ...
    def update_status(self, session_id: str, status: str, *, expected_version: int) -> bool: ...
    def set_conversation(
        self, session_id: str, conversation_id: str, runtime_mode: str
    ) -> None: ...
    def mark_goal_synced(self, session_id: str, synced_at: datetime) -> None: ...
    def list_recoverable(self) -> list[StoredSession]: ...


class EvidenceRepository(Protocol):
    def add(self, record: StoredEvidence) -> StoredEvidence: ...
    def get(self, session_id: str, evidence_id: str) -> StoredEvidence | None: ...
    def list_for_session(self, session_id: str) -> list[StoredEvidence]: ...
    def get_media_message_artifact(
        self,
        verified_user_id: str,
        session_id: str,
        evidence_id: str,
    ) -> MediaMessageArtifactFacts: ...
    def mark_synced(self, session_id: str, evidence_id: str, synced_at: datetime) -> None: ...


class AnswerRepository(Protocol):
    def get(self, session_id: str, question_id: str) -> StoredAnswer | None: ...
    def upsert(self, session_id: str, question_id: str, answer: str) -> StoredAnswer: ...
    def list_for_session(self, session_id: str) -> list[StoredAnswer]: ...
    def mark_synced(
        self,
        session_id: str,
        question_id: str,
        version: int,
        synced_at: datetime,
    ) -> None: ...


class AuditEventRepository(Protocol):
    def append(
        self,
        session_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        *,
        source_openhands_event_id: str | None,
        event_id: str | None = None,
    ) -> StoredAuditEvent: ...
    def list(self, session_id: str) -> list[StoredAuditEvent]: ...
    def latest(self, session_id: str) -> StoredAuditEvent | None: ...
    def has_source_event(self, session_id: str, source_event_id: str) -> bool: ...


class ReviewRepository(Protocol):
    def add_from_native_event(self, record: StoredReview) -> StoredReview: ...
    def list_for_session(self, session_id: str) -> list[StoredReview]: ...


class PrincipalRepository(Protocol):
    def add(self, record: StoredPrincipal) -> StoredPrincipal: ...
    def get_exact(self, *, issuer: str, subject: str) -> StoredPrincipal | None: ...
    def set_active(self, principal_id: str, *, active: bool) -> bool: ...


class SecurityAuditRepository(Protocol):
    def add(self, record: StoredSecurityAuditEvent) -> StoredSecurityAuditEvent: ...
    def delete_expired(self, *, cutoff: datetime, limit: int) -> int: ...


class SqlPrincipalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: StoredPrincipal) -> StoredPrincipal:
        self._session.add(
            VerifiedPrincipalModel(
                principal_id=record.principal_id,
                issuer=record.issuer,
                subject=record.subject,
                active=record.active,
                created_at=record.created_at,
                state_changed_at=record.state_changed_at,
            )
        )
        self._session.flush()
        return record

    def get_exact(self, *, issuer: str, subject: str) -> StoredPrincipal | None:
        model = self._session.scalar(
            select(VerifiedPrincipalModel).where(
                VerifiedPrincipalModel.issuer == issuer,
                VerifiedPrincipalModel.subject == subject,
            )
        )
        return _stored_principal(model) if model is not None else None

    def set_active(self, principal_id: str, *, active: bool) -> bool:
        changed_at = datetime.now(UTC)
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(VerifiedPrincipalModel)
                .where(
                    VerifiedPrincipalModel.principal_id == principal_id,
                    VerifiedPrincipalModel.active != active,
                )
                .values(active=active, state_changed_at=changed_at)
            ),
        )
        return bool(result.rowcount)


class SqlSecurityAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: StoredSecurityAuditEvent) -> StoredSecurityAuditEvent:
        self._session.add(
            SecurityAuditEventModel(
                id=record.id,
                request_id=record.request_id,
                principal_id=record.principal_id,
                token_fingerprint=record.token_fingerprint,
                outcome=record.outcome,
                reason_category=record.reason_category,
                occurred_at=record.occurred_at,
            )
        )
        self._session.flush()
        return record

    def delete_expired(self, *, cutoff: datetime, limit: int) -> int:
        expired_ids = list(
            self._session.scalars(
                select(SecurityAuditEventModel.id)
                .where(SecurityAuditEventModel.occurred_at < cutoff)
                .order_by(SecurityAuditEventModel.occurred_at, SecurityAuditEventModel.id)
                .limit(limit)
            )
        )
        if not expired_ids:
            return 0
        result = cast(
            CursorResult[Any],
            self._session.execute(delete(SecurityAuditEventModel).where(SecurityAuditEventModel.id.in_(expired_ids))),
        )
        return int(result.rowcount or 0)


class SqlSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, record: StoredSession) -> StoredSession:
        self._session.add(
            LearningSessionModel(
                session_id=record.session_id,
                owner_user_id=record.owner_user_id,
                status=record.status,
                adapter_mode=record.adapter_mode,
                domain=record.domain,
                title=record.title,
                goal=record.goal,
                expected_output=record.expected_output,
                planned_minutes=record.planned_minutes,
                conversation_id=record.conversation_id,
                runtime_mode=record.runtime_mode,
                review_result_json=record.review_result,
                goal_conversation_synced_at=record.goal_conversation_synced_at,
                version=record.version,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )
        self._session.flush()
        return record

    def get(self, session_id: str) -> StoredSession | None:
        model = self._session.get(LearningSessionModel, session_id)
        return _stored_session(model) if model is not None else None

    def get_owned(self, session_id: str, owner_user_id: str) -> StoredSession | None:
        model = self._session.scalar(
            select(LearningSessionModel).where(
                LearningSessionModel.session_id == session_id,
                LearningSessionModel.owner_user_id == owner_user_id,
            )
        )
        return _stored_session(model) if model is not None else None

    def update_status(self, session_id: str, status: str, *, expected_version: int) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(LearningSessionModel)
                .where(
                    LearningSessionModel.session_id == session_id,
                    LearningSessionModel.status != "reviewed",
                    LearningSessionModel.version == expected_version,
                )
                .values(
                    status=status,
                    version=LearningSessionModel.version + 1,
                    updated_at=datetime.now(UTC),
                )
            ),
        )
        return bool(result.rowcount)

    def set_conversation(self, session_id: str, conversation_id: str, runtime_mode: str) -> None:
        self._session.execute(
            update(LearningSessionModel)
            .where(LearningSessionModel.session_id == session_id)
            .values(
                conversation_id=conversation_id,
                runtime_mode=runtime_mode,
                updated_at=datetime.now(UTC),
            )
        )

    def mark_goal_synced(self, session_id: str, synced_at: datetime) -> None:
        self._session.execute(
            update(LearningSessionModel)
            .where(LearningSessionModel.session_id == session_id)
            .values(goal_conversation_synced_at=synced_at, updated_at=synced_at)
        )

    def list_recoverable(self) -> list[StoredSession]:
        models = self._session.scalars(
            select(LearningSessionModel)
            .where(LearningSessionModel.status.not_in(("reviewed", "failed")))
            .order_by(LearningSessionModel.created_at, LearningSessionModel.session_id)
        )
        return [_stored_session(model) for model in models]


class SqlEvidenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: StoredEvidence) -> StoredEvidence:
        self._session.add(
            EvidenceModel(
                evidence_id=record.evidence_id,
                session_id=record.session_id,
                evidence_type=record.evidence_type,
                content_hash=record.content_hash,
                text_content=record.text_content,
                source_url=record.source_url,
                metadata_json=record.metadata,
                conversation_synced_at=record.conversation_synced_at,
                created_at=record.created_at,
            )
        )
        self._session.flush()
        return record

    def get(self, session_id: str, evidence_id: str) -> StoredEvidence | None:
        model = self._session.scalar(
            select(EvidenceModel).where(
                EvidenceModel.session_id == session_id,
                EvidenceModel.evidence_id == evidence_id,
            )
        )
        return _stored_evidence(model) if model is not None else None

    def list_for_session(self, session_id: str) -> list[StoredEvidence]:
        models = self._session.scalars(
            select(EvidenceModel)
            .where(EvidenceModel.session_id == session_id)
            .order_by(EvidenceModel.created_at, EvidenceModel.evidence_id)
        )
        return [_stored_evidence(model) for model in models]

    def get_media_message_artifact(
        self,
        verified_user_id: str,
        session_id: str,
        evidence_id: str,
    ) -> MediaMessageArtifactFacts:
        stable_scan_key = (
            LearningSessionModel.session_id
            + ":"
            + MediaIngestionReservationModel.idempotency_key
            + ":"
            + MediaIngestionReservationModel.fingerprint
        )
        row = self._session.execute(
            select(
                LearningSessionModel.owner_user_id,
                EvidenceModel.evidence_id,
                EvidenceModel.artifact_id,
                MediaIngestionReservationModel.attributes_json,
                MediaArtifactModel.media_item_id,
                MediaArtifactModel.opaque_object_key,
                MediaArtifactModel.media_type,
                MediaArtifactModel.normalized_sha256,
                MediaArtifactModel.normalized_byte_size,
                MediaArtifactModel.state,
                MediaIngestionReservationModel.reservation_id,
                MediaScanAttemptModel.attempt_id,
                MediaScanAttemptModel.scan_result,
                MediaScanAttemptModel.artifact_sha256,
                MediaCleanReceiptModel.receipt_id,
            )
            .select_from(LearningSessionModel)
            .outerjoin(
                EvidenceModel,
                (EvidenceModel.session_id == LearningSessionModel.session_id)
                & (EvidenceModel.evidence_id == evidence_id),
            )
            .outerjoin(MediaArtifactModel, EvidenceModel.artifact_id == MediaArtifactModel.media_item_id)
            .outerjoin(
                MediaIngestionReservationModel,
                (MediaIngestionReservationModel.session_id == EvidenceModel.session_id)
                & (MediaIngestionReservationModel.evidence_id == EvidenceModel.evidence_id)
                & (MediaIngestionReservationModel.status == "COMPLETED"),
            )
            .outerjoin(MediaScanAttemptModel, MediaScanAttemptModel.idempotency_key == stable_scan_key)
            .outerjoin(MediaCleanReceiptModel, MediaCleanReceiptModel.attempt_id == MediaScanAttemptModel.attempt_id)
            .where(LearningSessionModel.session_id == session_id)
        ).one_or_none()
        if row is None:
            raise KeyError("session is unavailable")
        if row.owner_user_id != verified_user_id:
            raise PermissionError("verified identity does not own session")
        if row.evidence_id is None:
            raise KeyError("evidence is unavailable")
        if row.artifact_id is None:
            raise KeyError("evidence has no media artifact")
        if row.media_item_id is None:
            raise KeyError("media artifact is unavailable")
        if row.state != "REFERENCED":
            raise KeyError("media artifact is not consumable")
        if row.reservation_id is None:
            raise KeyError("clean receipt reservation is unavailable")
        if row.attempt_id is None or row.scan_result != "clean":
            raise KeyError("clean receipt is unavailable")
        if row.receipt_id is None:
            raise KeyError("clean receipt is unavailable")
        attributes = row.attributes_json if isinstance(row.attributes_json, dict) else {}
        return MediaMessageArtifactFacts(
            evidence_id=row.evidence_id,
            receipt_id=row.receipt_id,
            attempt_id=row.attempt_id,
            scan_result=row.scan_result,
            artifact_ref=f"focusproof-artifact://{row.media_item_id}",
            artifact_sha256=row.artifact_sha256,
            opaque_object_key=row.opaque_object_key,
            media_type=row.media_type,
            normalized_sha256=row.normalized_sha256,
            byte_size=row.normalized_byte_size,
            width=_positive_dimension(attributes.get("width")),
            height=_positive_dimension(attributes.get("height")),
        )

    def mark_synced(self, session_id: str, evidence_id: str, synced_at: datetime) -> None:
        self._session.execute(
            update(EvidenceModel)
            .where(
                EvidenceModel.session_id == session_id,
                EvidenceModel.evidence_id == evidence_id,
            )
            .values(conversation_synced_at=synced_at)
        )


class SqlAnswerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, session_id: str, question_id: str) -> StoredAnswer | None:
        model = self._session.get(LearnerAnswerModel, (session_id, question_id))
        return _stored_answer(model) if model is not None else None

    def upsert(self, session_id: str, question_id: str, answer: str) -> StoredAnswer:
        now = datetime.now(UTC)
        model = self._session.get(LearnerAnswerModel, (session_id, question_id))
        if model is None:
            model = LearnerAnswerModel(
                session_id=session_id,
                question_id=question_id,
                answer=answer,
                version=1,
                created_at=now,
                updated_at=now,
            )
            self._session.add(model)
        elif model.answer != answer:
            model.answer = answer
            model.version += 1
            model.conversation_synced_at = None
            model.updated_at = now
        self._session.flush()
        return _stored_answer(model)

    def list_for_session(self, session_id: str) -> list[StoredAnswer]:
        models = self._session.scalars(
            select(LearnerAnswerModel)
            .where(LearnerAnswerModel.session_id == session_id)
            .order_by(LearnerAnswerModel.created_at, LearnerAnswerModel.question_id)
        )
        return [_stored_answer(model) for model in models]

    def mark_synced(
        self,
        session_id: str,
        question_id: str,
        version: int,
        synced_at: datetime,
    ) -> None:
        self._session.execute(
            update(LearnerAnswerModel)
            .where(
                LearnerAnswerModel.session_id == session_id,
                LearnerAnswerModel.question_id == question_id,
                LearnerAnswerModel.version == version,
            )
            .values(conversation_synced_at=synced_at)
        )


class SqlAuditEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        session_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        *,
        source_openhands_event_id: str | None,
        event_id: str | None = None,
    ) -> StoredAuditEvent:
        _lock_learning_session(self._session, session_id)
        if event_id is not None:
            existing_model = self._session.get(AuditEventModel, event_id)
            if existing_model is not None:
                return _stored_audit_event(existing_model)
        if source_openhands_event_id is not None:
            existing = self._by_source(session_id, source_openhands_event_id)
            if existing is not None:
                return existing
        latest_sequence = self._session.scalar(
            select(func.max(AuditEventModel.sequence)).where(
                AuditEventModel.session_id == session_id
            )
        )
        model = AuditEventModel(
            event_id=event_id or f"evt_{uuid4().hex}",
            session_id=session_id,
            sequence=(latest_sequence or 0) + 1,
            type=event_type,
            actor=actor,
            payload_json=payload,
            source_openhands_event_id=source_openhands_event_id,
            created_at=datetime.now(UTC),
        )
        self._session.add(model)
        self._session.flush()
        return _stored_audit_event(model)

    def list(self, session_id: str) -> list[StoredAuditEvent]:
        models = self._session.scalars(
            select(AuditEventModel)
            .where(AuditEventModel.session_id == session_id)
            .order_by(AuditEventModel.sequence)
        )
        return [_stored_audit_event(model) for model in models]

    def latest(self, session_id: str) -> StoredAuditEvent | None:
        model = self._session.scalar(
            select(AuditEventModel)
            .where(AuditEventModel.session_id == session_id)
            .order_by(AuditEventModel.sequence.desc())
            .limit(1)
        )
        return _stored_audit_event(model) if model is not None else None

    def has_source_event(self, session_id: str, source_event_id: str) -> bool:
        return self._by_source(session_id, source_event_id) is not None

    def _by_source(self, session_id: str, source_event_id: str) -> StoredAuditEvent | None:
        model = self._session.scalar(
            select(AuditEventModel).where(
                AuditEventModel.session_id == session_id,
                AuditEventModel.source_openhands_event_id == source_event_id,
            )
        )
        return _stored_audit_event(model) if model is not None else None


def _lock_learning_session(
    session: Session,
    session_id: str,
) -> LearningSessionModel | None:
    return session.scalar(
        select(LearningSessionModel)
        .where(LearningSessionModel.session_id == session_id)
        .with_for_update()
    )


class SqlReviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_from_native_event(self, record: StoredReview) -> StoredReview:
        session = _lock_learning_session(self._session, record.session_id)
        if record.source_openhands_event_id is not None:
            existing = self._session.scalar(
                select(ReviewModel).where(
                    ReviewModel.session_id == record.session_id,
                    ReviewModel.source_openhands_event_id == record.source_openhands_event_id,
                )
            )
            if existing is not None:
                return _stored_review(existing)
        model = ReviewModel(
            review_id=record.review_id,
            session_id=record.session_id,
            conversation_id=record.conversation_id,
            review_status=record.review_status,
            score=record.score,
            result_json=record.result,
            native_event_count=record.native_event_count,
            source_openhands_event_id=record.source_openhands_event_id,
            created_at=record.created_at,
        )
        self._session.add(model)
        if session is not None and session.status != "reviewed":
            session.review_result_json = record.result
            session.status = (
                "reviewed" if record.review_status == "completed" else record.review_status
            )
            session.updated_at = datetime.now(UTC)
            session.version += 1
        self._session.flush()
        return _stored_review(model)

    def list_for_session(self, session_id: str) -> list[StoredReview]:
        models = self._session.scalars(
            select(ReviewModel)
            .where(ReviewModel.session_id == session_id)
            .order_by(ReviewModel.created_at, ReviewModel.review_id)
        )
        return [_stored_review(model) for model in models]


def _positive_dimension(value: object) -> int:
    if isinstance(value, bool):
        raise KeyError("media dimensions are unavailable")
    if isinstance(value, (int, float)) and int(value) == value and int(value) > 0:
        return int(value)
    raise KeyError("media dimensions are unavailable")


def _stored_session(model: LearningSessionModel) -> StoredSession:
    return StoredSession(
        session_id=model.session_id,
        owner_user_id=model.owner_user_id,
        status=model.status,
        adapter_mode=model.adapter_mode,
        domain=model.domain,
        title=model.title,
        goal=model.goal,
        expected_output=model.expected_output,
        planned_minutes=model.planned_minutes,
        conversation_id=model.conversation_id,
        runtime_mode=model.runtime_mode,
        review_result=model.review_result_json,
        goal_conversation_synced_at=model.goal_conversation_synced_at,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _stored_evidence(model: EvidenceModel) -> StoredEvidence:
    return StoredEvidence(
        evidence_id=model.evidence_id,
        session_id=model.session_id,
        evidence_type=model.evidence_type,
        content_hash=model.content_hash,
        text_content=model.text_content,
        source_url=model.source_url,
        metadata=model.metadata_json,
        conversation_synced_at=model.conversation_synced_at,
        created_at=model.created_at,
    )


def _stored_answer(model: LearnerAnswerModel) -> StoredAnswer:
    return StoredAnswer(
        session_id=model.session_id,
        question_id=model.question_id,
        answer=model.answer,
        version=model.version,
        conversation_synced_at=model.conversation_synced_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _stored_audit_event(model: AuditEventModel) -> StoredAuditEvent:
    return StoredAuditEvent(
        event_id=model.event_id,
        session_id=model.session_id,
        sequence=model.sequence,
        type=model.type,
        actor=model.actor,
        payload=model.payload_json,
        source_openhands_event_id=model.source_openhands_event_id,
        created_at=model.created_at,
    )


def _stored_review(model: ReviewModel) -> StoredReview:
    return StoredReview(
        review_id=model.review_id,
        session_id=model.session_id,
        conversation_id=model.conversation_id,
        review_status=model.review_status,
        score=model.score,
        result=model.result_json,
        native_event_count=model.native_event_count,
        source_openhands_event_id=model.source_openhands_event_id,
        created_at=model.created_at,
    )


def _stored_principal(model: VerifiedPrincipalModel) -> StoredPrincipal:
    return StoredPrincipal(
        principal_id=model.principal_id,
        issuer=model.issuer,
        subject=model.subject,
        active=model.active,
        created_at=model.created_at,
        state_changed_at=model.state_changed_at,
    )


class SpeechQuotaExceededError(ValueError):
    pass


class SpeechHmacReadinessError(RuntimeError):
    pass


class SpeechLeaseStateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SpeechAdmissionToken:
    request_id: str
    owner_user_id: str
    session_id: str
    lease_owner: str
    lease_generation: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class ResourceSlotLease:
    resource_kind: str
    slot_number: int
    lease_owner_token: str
    lease_generation: int


class SpeechRequestRepository(Protocol):
    def admit(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        idempotency_key: str,
        request_fingerprint: str | None,
        lease_owner: str,
        now: datetime | None = None,
        lease_seconds: int = 120,
    ) -> SpeechAdmissionToken: ...

    def transition(
        self,
        token: SpeechAdmissionToken,
        state: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 120,
        media_type: str | None = None,
        byte_size: int | None = None,
        duration_ms: int | None = None,
    ) -> SpeechAdmissionToken: ...

    def mark_dispatching(
        self,
        token: SpeechAdmissionToken,
        *,
        now: datetime | None = None,
        lease_seconds: int = 120,
    ) -> SpeechAdmissionToken: ...

    def finalize(
        self,
        token: SpeechAdmissionToken,
        *,
        state: str,
        outcome_code: str | None = None,
        latency_ms: int | None = None,
        now: datetime | None = None,
    ) -> bool: ...

    def recover_expired(self, *, now: datetime | None = None) -> int: ...


class ResourceSlotRepository(Protocol):
    def reconcile(
        self,
        resource_kind: str,
        *,
        configured_count: int,
        config_generation: int,
    ) -> None: ...

    def claim(
        self,
        resource_kind: str,
        *,
        work_kind: str,
        work_id: str,
        lease_seconds: int,
        now: datetime | None = None,
        timeout_ms: int | None = None,
    ) -> ResourceSlotLease | None: ...

    def release(self, lease: ResourceSlotLease, *, timeout_ms: int | None = None) -> bool: ...


_ACTIVE_SPEECH_STATES = {
    TranscriptionState.ADMITTED.value,
    TranscriptionState.UPLOADING.value,
    TranscriptionState.SCANNING.value,
    TranscriptionState.INSPECTING.value,
    TranscriptionState.DISPATCHING.value,
}
_TERMINAL_SPEECH_STATES = {
    TranscriptionState.SUCCEEDED.value,
    TranscriptionState.FAILED_TERMINAL.value,
    TranscriptionState.CANCELLED.value,
    TranscriptionState.AMBIGUOUS.value,
}
_SPEECH_OUTCOME_CODES = {
    "invalid_audio",
    "audio_too_large",
    "audio_too_long",
    "unsupported_audio_format",
    "malware_detected",
    "scan_unavailable",
    "inspection_failed",
    "client_cancelled",
    "transcription_timeout",
    "transcription_rate_limited",
    "transcription_provider_unavailable",
    "transcription_no_speech",
    "transcription_failed",
    "transcription_ambiguous",
    "lease_expired_pre_dispatch",
    "lease_expired_post_dispatch",
    "shutdown",
    "upload_failed",
}
_NEXT_SPEECH_STATE = {
    TranscriptionState.ADMITTED.value: TranscriptionState.UPLOADING.value,
    TranscriptionState.UPLOADING.value: TranscriptionState.SCANNING.value,
    TranscriptionState.SCANNING.value: TranscriptionState.INSPECTING.value,
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _advisory_key(namespace: str, value: str) -> int:
    digest = hashlib.sha256(f"{namespace}:{value}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _begin_immediate(session: Session) -> None:
    if session.get_bind().dialect.name != "sqlite":
        return
    marker = "speech_begin_immediate"
    if session.info.get(marker):
        return
    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    session.info[marker] = True


def _configure_postgres_transaction(session: Session, timeout_ms: int) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    bounded = max(1, min(timeout_ms, 115_000))
    session.execute(text(f"SET LOCAL lock_timeout = '{bounded}ms'"))
    session.execute(text(f"SET LOCAL statement_timeout = '{bounded}ms'"))


def _take_advisory_lock(session: Session, namespace: str, value: str) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _advisory_key(namespace, value)},
        )


class SqlSpeechRequestRepository:
    def __init__(
        self,
        session: Session,
        *,
        active_hmac_key_version: str | None = None,
        hmac_keys: Mapping[str, bytes] | None = None,
        lock_timeout_ms: int = 2_000,
        max_session_requests: int = 20,
        max_owner_hour_requests: int = 30,
    ) -> None:
        actual_keys = dict(hmac_keys or {})
        if active_hmac_key_version is not None and not active_hmac_key_version.strip():
            raise ValueError("active HMAC key version must not be blank")
        if (
            active_hmac_key_version is not None
            and active_hmac_key_version not in actual_keys
        ):
            raise ValueError("active HMAC key material is unavailable")
        if any(
            not version.strip() or not key for version, key in actual_keys.items()
        ):
            raise ValueError("HMAC keyring entries must be non-empty")
        self._session = session
        self._active_hmac_key_version = active_hmac_key_version
        self._hmac_keys = actual_keys
        self._lock_timeout_ms = lock_timeout_ms
        self._max_session_requests = max_session_requests
        self._max_owner_hour_requests = max_owner_hour_requests

    def admit(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        idempotency_key: str,
        request_fingerprint: str | None,
        lease_owner: str,
        now: datetime | None = None,
        lease_seconds: int = 120,
    ) -> SpeechAdmissionToken:
        if self._active_hmac_key_version is None:
            raise SpeechHmacReadinessError("speech HMAC keyring is not configured")

        UUID(idempotency_key)
        if request_fingerprint is not None and len(request_fingerprint) != 64:
            raise ValueError("request fingerprint must be a SHA-256 hex digest")
        if not lease_owner.strip() or lease_seconds <= 0:
            raise ValueError("speech lease must be bounded and owned")
        actual_now = _aware(now or datetime.now(UTC))
        self._lock_admission(owner_user_id, session_id)
        owned_session = self._session.get(LearningSessionModel, session_id)
        if owned_session is None or owned_session.owner_user_id != owner_user_id:
            raise SpeechAdmissionError(SpeechErrorCode.TRANSCRIPTION_FAILED)

        existing = self._find_duplicate(owner_user_id, session_id, idempotency_key)
        if existing is not None:
            self._raise_duplicate(existing, request_fingerprint)

        session_count = int(
            self._session.scalar(
                select(func.count(SpeechTranscriptionRequestModel.request_id)).where(
                    SpeechTranscriptionRequestModel.session_id == session_id
                )
            )
            or 0
        )
        owner_count = int(
            self._session.scalar(
                select(func.count(SpeechTranscriptionRequestModel.request_id)).where(
                    SpeechTranscriptionRequestModel.owner_user_id == owner_user_id,
                    SpeechTranscriptionRequestModel.created_at
                    >= actual_now - timedelta(hours=1),
                )
            )
            or 0
        )
        if session_count >= self._max_session_requests:
            raise SpeechQuotaExceededError("speech session lifetime quota exceeded")
        if owner_count >= self._max_owner_hour_requests:
            raise SpeechQuotaExceededError("speech owner rolling quota exceeded")

        active_version = self._active_hmac_key_version
        request_id = str(uuid4())
        expires_at = actual_now + timedelta(seconds=lease_seconds)
        row = SpeechTranscriptionRequestModel(
            request_id=request_id,
            session_id=session_id,
            owner_user_id=owner_user_id,
            idempotency_key_hash=self._hash(
                active_version, idempotency_key
            ),
            hmac_key_version=active_version,
            request_fingerprint=request_fingerprint,
            state=TranscriptionState.ADMITTED.value,
            media_type=None,
            byte_size=None,
            duration_ms=None,
            provider="dashscope",
            model="qwen3-asr-flash",
            provider_attempts=0,
            lease_owner=lease_owner,
            lease_generation=1,
            lease_expires_at=expires_at,
            provider_dispatched_at=None,
            outcome_code=None,
            latency_ms=None,
            created_at=actual_now,
            updated_at=actual_now,
            completed_at=None,
        )
        self._session.add(row)
        self._session.flush()
        return self._token(row)

    def assert_hmac_readiness(self) -> None:
        if self._active_hmac_key_version is None:
            raise SpeechHmacReadinessError("speech HMAC keyring is not configured")

        referenced = set(
            self._session.scalars(
                select(SpeechTranscriptionRequestModel.hmac_key_version).distinct()
            )
        )
        if referenced.difference(self._hmac_keys):
            raise SpeechHmacReadinessError(
                "speech HMAC key material is unavailable for retained metadata"
            )

    def transition(
        self,
        token: SpeechAdmissionToken,
        state: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 120,
        media_type: str | None = None,
        byte_size: int | None = None,
        duration_ms: int | None = None,
    ) -> SpeechAdmissionToken:
        if state not in {
            TranscriptionState.UPLOADING.value,
            TranscriptionState.SCANNING.value,
            TranscriptionState.INSPECTING.value,
        }:
            raise SpeechLeaseStateError("transition target is not an active business state")
        row = self._cas_row(token)
        if row is None or _NEXT_SPEECH_STATE.get(row.state) != state:
            raise SpeechLeaseStateError("speech transition is stale or illegal")
        actual_now = _aware(now or datetime.now(UTC))
        values: dict[str, object] = {
            "state": state,
            "lease_generation": token.lease_generation + 1,
            "lease_expires_at": actual_now + timedelta(seconds=lease_seconds),
            "updated_at": actual_now,
        }
        if media_type is not None:
            values["media_type"] = media_type
        if byte_size is not None:
            values["byte_size"] = byte_size
        if duration_ms is not None:
            values["duration_ms"] = duration_ms
        if not self._cas_update(token, values):
            raise SpeechLeaseStateError("speech transition lost its lease")
        return SpeechAdmissionToken(
            token.request_id,
            token.owner_user_id,
            token.session_id,
            token.lease_owner,
            token.lease_generation + 1,
            cast(datetime, values["lease_expires_at"]),
        )

    def mark_dispatching(
        self,
        token: SpeechAdmissionToken,
        *,
        now: datetime | None = None,
        lease_seconds: int = 120,
    ) -> SpeechAdmissionToken:
        row = self._cas_row(token)
        if row is None or row.state != TranscriptionState.INSPECTING.value:
            raise SpeechLeaseStateError("only inspected audio may be dispatched")
        actual_now = _aware(now or datetime.now(UTC))
        expires_at = actual_now + timedelta(seconds=lease_seconds)
        if not self._cas_update(
            token,
            {
                "state": TranscriptionState.DISPATCHING.value,
                "provider_attempts": 1,
                "provider_dispatched_at": actual_now,
                "lease_generation": token.lease_generation + 1,
                "lease_expires_at": expires_at,
                "updated_at": actual_now,
            },
        ):
            raise SpeechLeaseStateError("speech dispatch lost its lease")
        return SpeechAdmissionToken(
            token.request_id,
            token.owner_user_id,
            token.session_id,
            token.lease_owner,
            token.lease_generation + 1,
            expires_at,
        )

    def finalize(
        self,
        token: SpeechAdmissionToken,
        *,
        state: str,
        outcome_code: str | None = None,
        latency_ms: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        if state not in _TERMINAL_SPEECH_STATES:
            raise SpeechLeaseStateError("speech final state is not terminal")
        row = self._cas_row(token)
        if row is None:
            return False
        dispatched = row.provider_dispatched_at is not None
        if state == TranscriptionState.SUCCEEDED.value:
            if not dispatched or outcome_code is not None or latency_ms is None:
                raise SpeechLeaseStateError("succeeded metadata is inconsistent")
        elif outcome_code not in _SPEECH_OUTCOME_CODES:
            raise SpeechLeaseStateError("terminal failure outcome is not bounded")
        if state == TranscriptionState.CANCELLED.value and dispatched:
            raise SpeechLeaseStateError("post-dispatch cancellation is ambiguous")
        if state == TranscriptionState.AMBIGUOUS.value and not dispatched:
            raise SpeechLeaseStateError("pre-dispatch work cannot be ambiguous")
        actual_now = _aware(now or datetime.now(UTC))
        return self._cas_update(
            token,
            {
                "state": state,
                "lease_owner": None,
                "lease_generation": token.lease_generation + 1,
                "lease_expires_at": None,
                "outcome_code": outcome_code,
                "latency_ms": latency_ms,
                "completed_at": actual_now,
                "updated_at": actual_now,
            },
        )

    def recover_expired(self, *, now: datetime | None = None) -> int:
        actual_now = _aware(now or datetime.now(UTC))
        rows = list(
            self._session.scalars(
                select(SpeechTranscriptionRequestModel)
                .where(
                    SpeechTranscriptionRequestModel.state.in_(_ACTIVE_SPEECH_STATES),
                    SpeechTranscriptionRequestModel.lease_expires_at <= actual_now,
                )
                .with_for_update()
            )
        )
        recovered = 0
        for row in rows:
            if row.lease_owner is None or row.lease_expires_at is None:
                continue
            token = self._token(row)
            dispatched = row.provider_dispatched_at is not None
            state = (
                TranscriptionState.AMBIGUOUS.value
                if dispatched
                else TranscriptionState.FAILED_TERMINAL.value
            )
            outcome = (
                "lease_expired_post_dispatch"
                if dispatched
                else "lease_expired_pre_dispatch"
            )
            if self.finalize(token, state=state, outcome_code=outcome, now=actual_now):
                recovered += 1
        return recovered

    def _lock_admission(self, owner_user_id: str, session_id: str) -> None:
        _begin_immediate(self._session)
        _configure_postgres_transaction(self._session, self._lock_timeout_ms)
        _take_advisory_lock(self._session, "speech-owner", owner_user_id)
        _take_advisory_lock(self._session, "speech-session", session_id)

    def _find_duplicate(
        self,
        owner_user_id: str,
        session_id: str,
        idempotency_key: str,
    ) -> SpeechTranscriptionRequestModel | None:
        for version, key in self._hmac_keys.items():
            digest = hmac.new(key, idempotency_key.encode(), hashlib.sha256).hexdigest()
            row = self._session.scalar(
                select(SpeechTranscriptionRequestModel)
                .where(
                    SpeechTranscriptionRequestModel.owner_user_id == owner_user_id,
                    SpeechTranscriptionRequestModel.session_id == session_id,
                    SpeechTranscriptionRequestModel.hmac_key_version == version,
                    SpeechTranscriptionRequestModel.idempotency_key_hash == digest,
                )
                .with_for_update()
            )
            if row is not None:
                return row
        return None

    def _raise_duplicate(
        self,
        row: SpeechTranscriptionRequestModel,
        request_fingerprint: str | None,
    ) -> None:
        if row.request_fingerprint != request_fingerprint:
            raise SpeechAdmissionError(SpeechErrorCode.IDEMPOTENCY_CONFLICT)
        if row.state in _ACTIVE_SPEECH_STATES:
            raise SpeechAdmissionError(SpeechErrorCode.TRANSCRIPTION_IN_PROGRESS)
        raise SpeechAdmissionError(SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE)

    def _cas_row(
        self, token: SpeechAdmissionToken
    ) -> SpeechTranscriptionRequestModel | None:
        return self._session.scalar(
            select(SpeechTranscriptionRequestModel)
            .where(
                SpeechTranscriptionRequestModel.request_id == token.request_id,
                SpeechTranscriptionRequestModel.lease_owner == token.lease_owner,
                SpeechTranscriptionRequestModel.lease_generation
                == token.lease_generation,
            )
            .with_for_update()
        )

    def _cas_update(
        self,
        token: SpeechAdmissionToken,
        values: Mapping[str, object],
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(SpeechTranscriptionRequestModel)
                .where(
                    SpeechTranscriptionRequestModel.request_id == token.request_id,
                    SpeechTranscriptionRequestModel.lease_owner == token.lease_owner,
                    SpeechTranscriptionRequestModel.lease_generation
                    == token.lease_generation,
                )
                .values(**values)
            ),
        )
        return result.rowcount == 1

    def _hash(self, version: str, idempotency_key: str) -> str:
        return hmac.new(
            self._hmac_keys[version],
            idempotency_key.encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _token(row: SpeechTranscriptionRequestModel) -> SpeechAdmissionToken:
        if row.lease_owner is None or row.lease_expires_at is None:
            raise SpeechLeaseStateError("speech request has no active lease")
        return SpeechAdmissionToken(
            row.request_id,
            row.owner_user_id,
            row.session_id,
            row.lease_owner,
            row.lease_generation,
            _aware(row.lease_expires_at),
        )


class SqlResourceSlotRepository:
    def __init__(self, session: Session, *, lock_timeout_ms: int = 2_000) -> None:
        self._session = session
        self._lock_timeout_ms = lock_timeout_ms

    def reconcile(
        self,
        resource_kind: str,
        *,
        configured_count: int,
        config_generation: int,
    ) -> None:
        self._validate_resource(resource_kind)
        if configured_count < 0 or config_generation <= 0:
            raise ValueError("resource slot configuration is invalid")
        self._serialize(resource_kind)
        rows = list(
            self._session.scalars(
                select(SpeechResourceSlotModel)
                .where(SpeechResourceSlotModel.resource_kind == resource_kind)
                .order_by(SpeechResourceSlotModel.slot_number)
                .with_for_update()
            )
        )
        by_number = {row.slot_number: row for row in rows}
        for slot_number in range(configured_count):
            row = by_number.get(slot_number)
            if row is None:
                self._session.add(
                    SpeechResourceSlotModel(
                        resource_kind=resource_kind,
                        slot_number=slot_number,
                        lease_owner_token=None,
                        work_kind=None,
                        work_id=None,
                        config_generation=config_generation,
                        enabled=True,
                        lease_generation=0,
                        lease_expires_at=None,
                    )
                )
            else:
                row.enabled = True
                row.config_generation = config_generation
        for row in rows:
            if row.slot_number >= configured_count:
                row.enabled = False
                row.config_generation = config_generation
        self._session.flush()

    def claim(
        self,
        resource_kind: str,
        *,
        work_kind: str,
        work_id: str,
        lease_seconds: int,
        now: datetime | None = None,
        timeout_ms: int | None = None,
    ) -> ResourceSlotLease | None:
        self._validate_resource(resource_kind)
        if work_kind not in {"image", "speech"} or not work_id or lease_seconds <= 0:
            raise ValueError("resource slot work metadata is invalid")
        self._serialize(resource_kind, timeout_ms=timeout_ms)
        actual_now = _aware(now or datetime.now(UTC))
        self._session.execute(
            update(SpeechResourceSlotModel)
            .where(
                SpeechResourceSlotModel.resource_kind == resource_kind,
                SpeechResourceSlotModel.lease_owner_token.is_not(None),
                SpeechResourceSlotModel.lease_expires_at <= actual_now,
            )
            .values(
                lease_owner_token=None,
                work_kind=None,
                work_id=None,
                lease_expires_at=None,
            )
        )
        statement = (
            select(SpeechResourceSlotModel)
            .where(
                SpeechResourceSlotModel.resource_kind == resource_kind,
                SpeechResourceSlotModel.enabled.is_(True),
                SpeechResourceSlotModel.lease_owner_token.is_(None),
            )
            .order_by(SpeechResourceSlotModel.slot_number)
            .limit(1)
        )
        if self._session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        else:
            statement = statement.with_for_update()
        row = self._session.scalar(statement)
        if row is None:
            return None
        token = str(uuid4())
        row.lease_owner_token = token
        row.work_kind = work_kind
        row.work_id = work_id
        row.lease_generation += 1
        row.lease_expires_at = actual_now + timedelta(seconds=lease_seconds)
        self._session.flush()
        return ResourceSlotLease(
            row.resource_kind,
            row.slot_number,
            token,
            row.lease_generation,
        )

    def release(self, lease: ResourceSlotLease, *, timeout_ms: int | None = None) -> bool:
        bounded_timeout_ms = self._bounded_timeout_ms(timeout_ms)
        _begin_immediate(self._session)
        _configure_postgres_transaction(self._session, bounded_timeout_ms)

        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(SpeechResourceSlotModel)
                .where(
                    SpeechResourceSlotModel.resource_kind == lease.resource_kind,
                    SpeechResourceSlotModel.slot_number == lease.slot_number,
                    SpeechResourceSlotModel.lease_owner_token == lease.lease_owner_token,
                    SpeechResourceSlotModel.lease_generation == lease.lease_generation,
                )
                .values(
                    lease_owner_token=None,
                    work_kind=None,
                    work_id=None,
                    lease_expires_at=None,
                )
            ),
        )
        return result.rowcount == 1

    def _serialize(self, resource_kind: str, *, timeout_ms: int | None = None) -> None:
        bounded_timeout_ms = self._bounded_timeout_ms(timeout_ms)
        _begin_immediate(self._session)
        _configure_postgres_transaction(self._session, bounded_timeout_ms)
        _take_advisory_lock(self._session, "speech-resource", resource_kind)

    def _bounded_timeout_ms(self, timeout_ms: int | None) -> int:
        if timeout_ms is None:
            return self._lock_timeout_ms
        return max(1, min(timeout_ms, self._lock_timeout_ms))

    @staticmethod
    def _validate_resource(resource_kind: str) -> None:
        if resource_kind not in {"scan", "asr"}:
            raise ValueError("resource kind must be scan or asr")
