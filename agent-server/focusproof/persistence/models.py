from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Boolean,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class VerifiedPrincipalModel(Base):
    __tablename__ = "verified_principals"
    __table_args__ = (
        UniqueConstraint(
            "issuer",
            "subject",
            name="uq_verified_principals_issuer_subject",
        ),
    )

    principal_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(2048), nullable=False)
    subject: Mapped[str] = mapped_column(String(1024), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    state_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class SecurityAuditEventModel(Base):
    __tablename__ = "security_audit_events"
    __table_args__ = (
        Index("ix_security_audit_events_occurred_at_id", "occurred_at", "id"),
        Index("ix_security_audit_events_principal_id", "principal_id"),
        UniqueConstraint("request_id", name="uq_security_audit_events_request_id"),
    )

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(96), nullable=False)
    principal_id: Mapped[str | None] = mapped_column(String(96))
    token_fingerprint: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_category: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearningSessionModel(Base):
    __tablename__ = "learning_sessions"

    session_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[str | None] = mapped_column(Text)
    planned_minutes: Mapped[int | None] = mapped_column(Integer)
    conversation_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    runtime_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    review_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    goal_conversation_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )


class EvidenceModel(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("session_id", "evidence_id"),
        Index("ix_evidence_session_content_hash", "session_id", "content_hash"),
    )

    evidence_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    evidence_type: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    text_content: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    conversation_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class LearnerAnswerModel(Base):
    __tablename__ = "learner_answers"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.session_id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    conversation_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )


class AuditEventModel(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence"),
        UniqueConstraint("session_id", "source_openhands_event_id"),
    )

    event_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_openhands_event_id: Mapped[str | None] = mapped_column(String(96))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class ReviewModel(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("session_id", "source_openhands_event_id"),
    )

    review_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    review_status: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    native_event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_openhands_event_id: Mapped[str | None] = mapped_column(String(96))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class MonadEvidenceClaimModel(Base):
    __tablename__ = "monad_evidence_claims"
    __table_args__ = (
        UniqueConstraint(
            "chain_id",
            "transaction_hash",
            name="uq_monad_claim_chain_transaction",
        ),
        Index(
            "ix_monad_claim_session_evidence",
            "session_id",
            "evidence_id",
        ),
    )

    claim_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence.evidence_id", ondelete="CASCADE"),
        nullable=False,
    )
    observation_event_id: Mapped[str] = mapped_column(String(96), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
