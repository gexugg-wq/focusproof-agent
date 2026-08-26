"""Add immutable media scan attempts and clean receipts.

Revision ID: 0006_media_scan_receipts
Revises: 0005_media_artifacts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from focusproof.contracts.media_scan import SCAN_RESULT_REJECTION_CODE_CHECK_SQL

revision: str = "0006_media_scan_receipts"
down_revision: str | None = "0005_media_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_attempts",
        sa.Column("attempt_id", sa.String(96), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("scanner_backend", sa.String(64), nullable=False),
        sa.Column("definitions_version", sa.String(255), nullable=False),
        sa.Column("definitions_fresh_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("definitions_age_seconds", sa.Integer(), nullable=False),
        sa.Column("max_bytes", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_scans", sa.Integer(), nullable=False),
        sa.Column("deadline_ms", sa.Integer(), nullable=False),
        sa.Column("socket_timeout_ms", sa.Integer(), nullable=False),
        sa.Column("scan_result", sa.String(32), nullable=False),
        sa.Column("rejection_code", sa.String(64), nullable=True),
        sa.Column("rejection_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.PrimaryKeyConstraint("attempt_id", name="pk_scan_attempts"),
        sa.UniqueConstraint("attempt_id", name="uq_scan_attempts_attempt_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_scan_attempts_idempotency_key"),
        sa.CheckConstraint(
            SCAN_RESULT_REJECTION_CODE_CHECK_SQL,
            name="ck_scan_attempts_result_rejection_code",
        ),
        sa.CheckConstraint(
            "scan_result <> 'clean' OR rejection_detail IS NULL",
            name="ck_scan_attempts_clean_rejection_detail",
        ),
        sa.CheckConstraint(
            "definitions_age_seconds >= 0 AND max_bytes > 0 "
            "AND max_concurrent_scans > 0 AND deadline_ms > 0 "
            "AND socket_timeout_ms > 0",
            name="ck_scan_attempts_resource_snapshot",
        ),
    )
    op.create_table(
        "clean_receipts",
        sa.Column("receipt_id", sa.String(96), nullable=False),
        sa.Column("attempt_id", sa.String(96), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("scanner_backend", sa.String(64), nullable=False),
        sa.Column("definitions_version", sa.String(255), nullable=False),
        sa.Column("definitions_fresh_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("definitions_age_seconds", sa.Integer(), nullable=False),
        sa.Column("max_bytes", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_scans", sa.Integer(), nullable=False),
        sa.Column("deadline_ms", sa.Integer(), nullable=False),
        sa.Column("socket_timeout_ms", sa.Integer(), nullable=False),
        sa.Column("quarantine_path", sa.String(1024), nullable=False),
        sa.Column("quarantine_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["scan_attempts.attempt_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("receipt_id", name="pk_clean_receipts"),
        sa.UniqueConstraint("attempt_id", name="uq_clean_receipts_attempt_id"),
        sa.UniqueConstraint("receipt_hash", name="uq_clean_receipts_receipt_hash"),
        sa.CheckConstraint(
            "definitions_age_seconds >= 0 AND max_bytes > 0 "
            "AND max_concurrent_scans > 0 AND deadline_ms > 0 "
            "AND socket_timeout_ms > 0",
            name="ck_clean_receipts_resource_snapshot",
        ),
    )
    op.create_table(
        "pending_clean_receipts",
        sa.Column("receipt_id", sa.String(96), nullable=False),
        sa.Column("attempt_id", sa.String(96), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("spool_token", sa.String(96), nullable=False),
        sa.Column("spool_byte_size", sa.Integer(), nullable=False),
        sa.Column("spool_sha256", sa.String(64), nullable=False),
        sa.Column("spool_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quarantine_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("formal_artifact_id", sa.String(32), nullable=False),
        sa.Column("publication_status", sa.String(32), nullable=False),
        sa.Column("publication_owner", sa.String(96), nullable=True),
        sa.Column("publication_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publication_version", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publication_failure", sa.String(128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["scan_attempts.attempt_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("receipt_id", name="pk_pending_clean_receipts"),
        sa.UniqueConstraint("attempt_id", name="uq_pending_clean_receipts_attempt_id"),
        sa.UniqueConstraint("receipt_hash", name="uq_pending_clean_receipts_receipt_hash"),
        sa.CheckConstraint(
            "publication_status IN ('pending', 'publishing', 'published', 'failed')",
            name="ck_pending_clean_receipts_publication_status",
        ),
        sa.CheckConstraint(
            "publication_version >= 0",
            name="ck_pending_clean_receipts_publication_version",
        ),
        sa.CheckConstraint(
            "((publication_status = 'publishing' "
            "AND publication_owner IS NOT NULL "
            "AND publication_lease_expires_at IS NOT NULL "
            "AND published_at IS NULL) "
            "OR (publication_status <> 'publishing' "
            "AND publication_owner IS NULL "
            "AND publication_lease_expires_at IS NULL))",
            name="ck_pending_clean_receipts_publication_owner",
        ),
        sa.CheckConstraint(
            "((publication_status = 'published' AND published_at IS NOT NULL) "
            "OR (publication_status <> 'published' AND published_at IS NULL))",
            name="ck_pending_clean_receipts_published_at",
        ),
    )


def downgrade() -> None:
    op.drop_table("pending_clean_receipts")
    op.drop_table("clean_receipts")
    op.drop_table("scan_attempts")
