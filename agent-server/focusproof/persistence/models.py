from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
    Boolean,
    CheckConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from focusproof.contracts.media_scan import SCAN_RESULT_REJECTION_CODE_CHECK_SQL

logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


_MEDIA_RESERVATION_DURABLE_FACTS_NULL = (
    "canonical_artifact_id IS NULL AND evidence_id IS NULL "
    "AND staged_object_key IS NULL AND staged_manifest_id IS NULL "
    "AND media_type IS NULL AND normalized_sha256 IS NULL "
    "AND normalized_byte_size IS NULL AND learner_explanation IS NULL "
    "AND attributes_json IS NULL AND result_json IS NULL"
)
_MEDIA_RESERVATION_DURABLE_FACTS_PRESENT = (
    "canonical_artifact_id IS NOT NULL AND evidence_id IS NOT NULL "
    "AND staged_object_key IS NOT NULL AND staged_manifest_id IS NOT NULL "
    "AND media_type IS NOT NULL AND normalized_sha256 IS NOT NULL "
    "AND normalized_byte_size IS NOT NULL AND normalized_byte_size >= 0 "
    "AND learner_explanation IS NOT NULL "
    "AND attributes_json IS NOT NULL "
    "AND CAST(attributes_json AS VARCHAR) <> 'null' "
    "AND result_json IS NOT NULL "
    "AND CAST(result_json AS VARCHAR) <> 'null'"
)
_MEDIA_RESERVATION_STATE_PAYLOAD_MATRIX = (
    "((status = 'ACTIVE' AND active IS TRUE AND completion_mode IS NULL "
    "AND intent_action IS NULL AND "
    f"{_MEDIA_RESERVATION_DURABLE_FACTS_NULL}) "
    "OR (status = 'PENDING_REFERENCE' AND active IS TRUE AND completion_mode IS NULL "
    "AND intent_action IS NOT NULL "
    "AND intent_action IN ('MARK_REFERENCED', 'ABORT_STAGED') AND "
    f"{_MEDIA_RESERVATION_DURABLE_FACTS_PRESENT}) "
    "OR (status = 'COMPLETED' AND active IS NULL "
    "AND completion_mode IS NOT NULL AND intent_action IS NOT NULL AND "
    f"{_MEDIA_RESERVATION_DURABLE_FACTS_PRESENT}) "
    "OR (status IN ('REJECTED', 'EXPIRED') AND active IS NULL "
    "AND completion_mode IS NULL AND intent_action IS NULL AND "
    f"{_MEDIA_RESERVATION_DURABLE_FACTS_NULL}))"
)


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
    goal_conversation_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        Index("ix_evidence_artifact_id", "artifact_id"),
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
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "media_artifacts.media_item_id",
            name="fk_evidence_artifact",
            ondelete="RESTRICT",
        )
    )


class MediaIngestionReservationModel(Base):
    __tablename__ = "media_ingestion_reservations"
    __table_args__ = (
        PrimaryKeyConstraint("reservation_id", name="pk_media_ingestion_reservations"),
        UniqueConstraint(
            "owner_id", "session_id", "idempotency_key", name="uq_media_ingestion_owner_session_key"
        ),
        Index(
            "uq_media_ingestion_active_owner_slot",
            "owner_id",
            "session_id",
            "slot",
            unique=True,
            sqlite_where=text("active IS TRUE"),
            postgresql_where=text("active IS TRUE"),
        ),
        Index("ix_media_ingestion_owner_status_expires", "owner_id", "status", "expires_at"),
        CheckConstraint("slot >= 0", name="ck_media_ingestion_slot_nonnegative"),
        CheckConstraint(
            "status IN ('ACTIVE', 'PENDING_REFERENCE', 'COMPLETED', 'REJECTED', 'EXPIRED')",
            name="ck_media_ingestion_status",
        ),
        CheckConstraint(
            "intent_action IS NULL OR intent_action IN ('MARK_REFERENCED', 'ABORT_STAGED')",
            name="ck_media_ingestion_intent_action",
        ),
        CheckConstraint(
            "((status IN ('ACTIVE', 'PENDING_REFERENCE') AND active IS TRUE) "
            "OR (status IN ('COMPLETED', 'REJECTED', 'EXPIRED') AND active IS NULL))",
            name="ck_media_ingestion_status_active",
        ),
        CheckConstraint(
            "((status = 'COMPLETED' AND ((completion_mode = 'ADOPTED' AND "
            "intent_action = 'MARK_REFERENCED') OR (completion_mode IN "
            "('FOLLOWER', 'DIRECT_REUSE') AND intent_action = 'ABORT_STAGED'))) "
            "OR (status <> 'COMPLETED' AND completion_mode IS NULL))",
            name="ck_media_ingestion_completion_mode",
        ),
        CheckConstraint(
            _MEDIA_RESERVATION_STATE_PAYLOAD_MATRIX,
            name="ck_media_ingestion_state_payload_matrix",
        ),
    )
    reservation_id: Mapped[str] = mapped_column(String(96))
    media_item_id: Mapped[str] = mapped_column(String(96), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[str] = mapped_column(
        ForeignKey(
            "learning_sessions.session_id",
            name="fk_media_ingestion_reservations_session",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool | None] = mapped_column(Boolean)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    canonical_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "media_artifacts.media_item_id",
            name="fk_media_ingestion_canonical_artifact",
            ondelete="SET NULL",
            use_alter=True,
        )
    )
    evidence_id: Mapped[str | None] = mapped_column(String(96))
    intent_action: Mapped[str | None] = mapped_column(String(32))
    completion_mode: Mapped[str | None] = mapped_column(String(32))
    staged_object_key: Mapped[str | None] = mapped_column(String(512))
    staged_manifest_id: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str | None] = mapped_column(String(128))
    normalized_sha256: Mapped[str | None] = mapped_column(String(64))
    normalized_byte_size: Mapped[int | None] = mapped_column(Integer)
    learner_explanation: Mapped[str | None] = mapped_column(Text)
    attributes_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class MediaArtifactModel(Base):
    __tablename__ = "media_artifacts"
    __table_args__ = (
        PrimaryKeyConstraint("media_item_id", name="pk_media_artifacts"),
        UniqueConstraint(
            "owner_id", "normalized_sha256", name="uq_media_artifacts_owner_normalized_hash"
        ),
        UniqueConstraint("opaque_object_key", name="uq_media_artifacts_object_key"),
        Index("ix_media_artifacts_owner_state", "owner_id", "state"),
        CheckConstraint(
            "state IN ('PENDING_REFERENCE', 'REFERENCED')",
            name="ck_media_artifacts_state",
        ),
        CheckConstraint(
            "normalized_byte_size >= 0",
            name="ck_media_artifacts_byte_size_nonnegative",
        ),
    )
    media_item_id: Mapped[str] = mapped_column(String(96))
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    creator_reservation_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "media_ingestion_reservations.reservation_id",
            name="fk_media_artifacts_reservation",
            ondelete="SET NULL",
        )
    )
    opaque_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    manifest_id: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )


class MediaScanAttemptModel(Base):
    __tablename__ = "scan_attempts"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_scan_attempts_attempt_id"),
        UniqueConstraint("idempotency_key", name="uq_scan_attempts_idempotency_key"),
        CheckConstraint(
            SCAN_RESULT_REJECTION_CODE_CHECK_SQL,
            name="ck_scan_attempts_result_rejection_code",
        ),
        CheckConstraint(
            "scan_result <> 'clean' OR rejection_detail IS NULL",
            name="ck_scan_attempts_clean_rejection_detail",
        ),
        CheckConstraint(
            "definitions_age_seconds >= 0 AND max_bytes > 0 "
            "AND max_concurrent_scans > 0 AND deadline_ms > 0 "
            "AND socket_timeout_ms > 0",
            name="ck_scan_attempts_resource_snapshot",
        ),
    )

    attempt_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    scanner_backend: Mapped[str] = mapped_column(String(64), nullable=False)
    definitions_version: Mapped[str] = mapped_column(String(255), nullable=False)
    definitions_fresh_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    definitions_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_concurrent_scans: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    socket_timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    scan_result: Mapped[str] = mapped_column(String(32), nullable=False)
    rejection_code: Mapped[str | None] = mapped_column(String(64))
    rejection_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)


class MediaCleanReceiptModel(Base):
    __tablename__ = "clean_receipts"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_clean_receipts_attempt_id"),
        UniqueConstraint("receipt_hash", name="uq_clean_receipts_receipt_hash"),
        CheckConstraint(
            "definitions_age_seconds >= 0 AND max_bytes > 0 "
            "AND max_concurrent_scans > 0 AND deadline_ms > 0 "
            "AND socket_timeout_ms > 0",
            name="ck_clean_receipts_resource_snapshot",
        ),
    )

    receipt_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("scan_attempts.attempt_id", ondelete="RESTRICT"), nullable=False
    )
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scanner_backend: Mapped[str] = mapped_column(String(64), nullable=False)
    definitions_version: Mapped[str] = mapped_column(String(255), nullable=False)
    definitions_fresh_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    definitions_age_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_concurrent_scans: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    socket_timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    quarantine_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    quarantine_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PendingCleanReceiptModel(Base):
    __tablename__ = "pending_clean_receipts"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_pending_clean_receipts_attempt_id"),
        UniqueConstraint("receipt_hash", name="uq_pending_clean_receipts_receipt_hash"),
        CheckConstraint(
            "publication_status IN ('pending', 'publishing', 'published', 'failed')",
            name="ck_pending_clean_receipts_publication_status",
        ),
        CheckConstraint(
            "publication_version >= 0",
            name="ck_pending_clean_receipts_publication_version",
        ),
        CheckConstraint(
            "((publication_status = 'publishing' "
            "AND publication_owner IS NOT NULL "
            "AND publication_lease_expires_at IS NOT NULL "
            "AND published_at IS NULL) "
            "OR (publication_status <> 'publishing' "
            "AND publication_owner IS NULL "
            "AND publication_lease_expires_at IS NULL))",
            name="ck_pending_clean_receipts_publication_owner",
        ),
        CheckConstraint(
            "((publication_status = 'published' AND published_at IS NOT NULL) "
            "OR (publication_status <> 'published' AND published_at IS NULL))",
            name="ck_pending_clean_receipts_published_at",
        ),
    )

    receipt_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("scan_attempts.attempt_id", ondelete="RESTRICT"), nullable=False
    )
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    spool_token: Mapped[str] = mapped_column(String(96), nullable=False)
    spool_byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    spool_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    spool_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quarantine_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    formal_artifact_id: Mapped[str] = mapped_column(String(32), nullable=False)
    publication_status: Mapped[str] = mapped_column(String(32), nullable=False)
    publication_owner: Mapped[str | None] = mapped_column(String(96))
    publication_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    publication_version: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publication_failure: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    __table_args__ = (UniqueConstraint("session_id", "source_openhands_event_id"),)

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
