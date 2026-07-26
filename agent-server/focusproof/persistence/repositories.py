from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.orm import Session

from focusproof.persistence.models import (
    AuditEventModel,
    EvidenceModel,
    LearnerAnswerModel,
    LearningSessionModel,
    ReviewModel,
    SecurityAuditEventModel,
    VerifiedPrincipalModel,
)
from focusproof.runtime.security_audit import (
    SecurityAuditOutcome,
    SecurityAuditReasonCategory,
)


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


class SessionRepository(Protocol):
    def create(self, record: StoredSession) -> StoredSession: ...
    def get(self, session_id: str) -> StoredSession | None: ...
    def get_owned(
        self, session_id: str, owner_user_id: str
    ) -> StoredSession | None: ...
    def update_status(
        self, session_id: str, status: str, *, expected_version: int
    ) -> bool: ...
    def set_conversation(
        self, session_id: str, conversation_id: str, runtime_mode: str
    ) -> None: ...
    def mark_goal_synced(self, session_id: str, synced_at: datetime) -> None: ...
    def list_recoverable(self) -> list[StoredSession]: ...


class EvidenceRepository(Protocol):
    def add(self, record: StoredEvidence) -> StoredEvidence: ...
    def get(self, session_id: str, evidence_id: str) -> StoredEvidence | None: ...
    def list_for_session(self, session_id: str) -> list[StoredEvidence]: ...
    def mark_synced(
        self, session_id: str, evidence_id: str, synced_at: datetime
    ) -> None: ...


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
                .order_by(
                    SecurityAuditEventModel.occurred_at,
                    SecurityAuditEventModel.id,
                )
                .limit(limit)
            )
        )
        if not expired_ids:
            return 0
        result = cast(
            CursorResult[Any],
            self._session.execute(
                delete(SecurityAuditEventModel).where(
                    SecurityAuditEventModel.id.in_(expired_ids)
                )
            ),
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

    def get_owned(
        self,
        session_id: str,
        owner_user_id: str,
    ) -> StoredSession | None:
        model = self._session.scalar(
            select(LearningSessionModel).where(
                LearningSessionModel.session_id == session_id,
                LearningSessionModel.owner_user_id == owner_user_id,
            )
        )
        return _stored_session(model) if model is not None else None

    def update_status(
        self, session_id: str, status: str, *, expected_version: int
    ) -> bool:
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

    def set_conversation(
        self, session_id: str, conversation_id: str, runtime_mode: str
    ) -> None:
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

    def mark_synced(
        self, session_id: str, evidence_id: str, synced_at: datetime
    ) -> None:
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

    def _by_source(
        self, session_id: str, source_event_id: str
    ) -> StoredAuditEvent | None:
        model = self._session.scalar(
            select(AuditEventModel).where(
                AuditEventModel.session_id == session_id,
                AuditEventModel.source_openhands_event_id == source_event_id,
            )
        )
        return _stored_audit_event(model) if model is not None else None


class SqlReviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_from_native_event(self, record: StoredReview) -> StoredReview:
        if record.source_openhands_event_id is not None:
            existing = self._session.scalar(
                select(ReviewModel).where(
                    ReviewModel.session_id == record.session_id,
                    ReviewModel.source_openhands_event_id
                    == record.source_openhands_event_id,
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
        session = self._session.get(LearningSessionModel, record.session_id)
        if session is not None:
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
