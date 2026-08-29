"""Add metadata-only speech request ledger and shared resource slots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_speech_requests"
down_revision: str | None = "0007_drop_monad_evidence_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_STATES = "'admitted', 'uploading', 'scanning', 'inspecting', 'dispatching'"
_TERMINAL_STATES = "'succeeded', 'failed_terminal', 'cancelled', 'ambiguous'"
_OUTCOME_CODES = (
    "'invalid_audio', 'audio_too_large', 'audio_too_long', "
    "'unsupported_audio_format', 'malware_detected', 'scan_unavailable', "
    "'inspection_failed', 'client_cancelled', 'transcription_timeout', "
    "'transcription_rate_limited', 'transcription_provider_unavailable', "
    "'transcription_no_speech', 'transcription_failed', "
    "'transcription_ambiguous', 'lease_expired_pre_dispatch', "
    "'lease_expired_post_dispatch', 'shutdown', 'upload_failed'"
)


def upgrade() -> None:
    op.create_table(
        "speech_transcription_requests",
        sa.Column("request_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column(
            "session_id",
            sa.String(96),
            sa.ForeignKey(
                "learning_sessions.session_id",
                name="fk_speech_requests_session",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("hmac_key_version", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(64)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("media_type", sa.String(128)),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("provider_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(96)),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("provider_dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("outcome_code", sa.String(64)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("request_id", name="pk_speech_transcription_requests"),
        sa.UniqueConstraint(
            "owner_user_id",
            "session_id",
            "hmac_key_version",
            "idempotency_key_hash",
            name="uq_speech_requests_owner_session_hmac",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64",
            name="ck_speech_requests_hmac_hash_length",
        ),
        sa.CheckConstraint(
            "length(trim(hmac_key_version)) > 0",
            name="ck_speech_requests_hmac_version",
        ),
        sa.CheckConstraint(
            "request_fingerprint IS NULL OR length(request_fingerprint) = 64",
            name="ck_speech_requests_fingerprint_length",
        ),
        sa.CheckConstraint(
            f"state IN ({_ACTIVE_STATES}, {_TERMINAL_STATES})",
            name="ck_speech_requests_state",
        ),
        sa.CheckConstraint(
            "provider = 'dashscope' AND model = 'qwen3-asr-flash'",
            name="ck_speech_requests_provider_model",
        ),
        sa.CheckConstraint(
            "provider_attempts BETWEEN 0 AND 1",
            name="ck_speech_requests_provider_attempts",
        ),
        sa.CheckConstraint(
            "lease_generation > 0",
            name="ck_speech_requests_lease_generation",
        ),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size > 0",
            name="ck_speech_requests_byte_size",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms > 0",
            name="ck_speech_requests_duration",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_speech_requests_latency",
        ),
        sa.CheckConstraint(
            f"((state IN ({_ACTIVE_STATES}) AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND completed_at IS NULL) OR "
            f"(state IN ({_TERMINAL_STATES}) AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND completed_at IS NOT NULL))",
            name="ck_speech_requests_lease_terminal_matrix",
        ),
        sa.CheckConstraint(
            "((state IN ('admitted', 'uploading', 'scanning', 'inspecting', "
            "'cancelled') AND provider_dispatched_at IS NULL AND provider_attempts = 0) "
            "OR (state IN ('dispatching', 'succeeded', 'ambiguous') "
            "AND provider_dispatched_at IS NOT NULL AND provider_attempts = 1) "
            "OR (state = 'failed_terminal' AND "
            "((provider_dispatched_at IS NULL AND provider_attempts = 0) OR "
            "(provider_dispatched_at IS NOT NULL AND provider_attempts = 1))))",
            name="ck_speech_requests_dispatch_matrix",
        ),
        sa.CheckConstraint(
            f"((state = 'succeeded' AND outcome_code IS NULL "
            "AND latency_ms IS NOT NULL) OR "
            f"(state IN ('failed_terminal', 'cancelled', 'ambiguous') "
            f"AND outcome_code IN ({_OUTCOME_CODES})) OR "
            f"(state IN ({_ACTIVE_STATES}) AND outcome_code IS NULL "
            "AND latency_ms IS NULL))",
            name="ck_speech_requests_outcome_matrix",
        ),
    )
    op.create_index(
        "ix_speech_requests_owner_created",
        "speech_transcription_requests",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_speech_requests_session_created",
        "speech_transcription_requests",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_speech_requests_state_lease",
        "speech_transcription_requests",
        ["state", "lease_expires_at"],
    )

    op.create_table(
        "speech_resource_slots",
        sa.Column("resource_kind", sa.String(32), nullable=False),
        sa.Column("slot_number", sa.Integer(), nullable=False),
        sa.Column("lease_owner_token", sa.String(96)),
        sa.Column("work_kind", sa.String(16)),
        sa.Column("work_id", sa.String(96)),
        sa.Column("config_generation", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint(
            "resource_kind",
            "slot_number",
            name="pk_speech_resource_slots",
        ),
        sa.CheckConstraint(
            "resource_kind IN ('scan', 'asr')",
            name="ck_speech_slots_resource_kind",
        ),
        sa.CheckConstraint(
            "slot_number >= 0 AND config_generation > 0 AND lease_generation >= 0",
            name="ck_speech_slots_positive_values",
        ),
        sa.CheckConstraint(
            "work_kind IS NULL OR work_kind IN ('image', 'speech')",
            name="ck_speech_slots_work_kind",
        ),
        sa.CheckConstraint(
            "((lease_owner_token IS NULL AND work_kind IS NULL AND work_id IS NULL "
            "AND lease_expires_at IS NULL) OR "
            "(lease_owner_token IS NOT NULL AND work_kind IS NOT NULL "
            "AND work_id IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_generation > 0))",
            name="ck_speech_slots_occupancy_matrix",
        ),
    )
    op.create_index(
        "ix_speech_slots_claim",
        "speech_resource_slots",
        ["resource_kind", "enabled", "slot_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_speech_slots_claim", table_name="speech_resource_slots")
    op.drop_table("speech_resource_slots")
    op.drop_index(
        "ix_speech_requests_state_lease",
        table_name="speech_transcription_requests",
    )
    op.drop_index(
        "ix_speech_requests_session_created",
        table_name="speech_transcription_requests",
    )
    op.drop_index(
        "ix_speech_requests_owner_created",
        table_name="speech_transcription_requests",
    )
    op.drop_table("speech_transcription_requests")
