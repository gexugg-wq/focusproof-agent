"""Add modality-neutral media reservations and artifacts."""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0005_media_artifacts"
down_revision: str | None = "0004_monad_evidence_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DURABLE_FACTS_NULL = (
    "canonical_artifact_id IS NULL AND evidence_id IS NULL "
    "AND staged_object_key IS NULL AND staged_manifest_id IS NULL "
    "AND media_type IS NULL AND normalized_sha256 IS NULL "
    "AND normalized_byte_size IS NULL AND learner_explanation IS NULL "
    "AND attributes_json IS NULL AND result_json IS NULL"
)
_DURABLE_FACTS_PRESENT = (
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
_STATE_PAYLOAD_MATRIX = (
    "((status = 'ACTIVE' AND active IS TRUE AND completion_mode IS NULL "
    "AND intent_action IS NULL AND "
    f"{_DURABLE_FACTS_NULL}) "
    "OR (status = 'PENDING_REFERENCE' AND active IS TRUE AND completion_mode IS NULL "
    "AND intent_action IS NOT NULL "
    "AND intent_action IN ('MARK_REFERENCED', 'ABORT_STAGED') AND "
    f"{_DURABLE_FACTS_PRESENT}) "
    "OR (status = 'COMPLETED' AND active IS NULL "
    "AND completion_mode IS NOT NULL AND intent_action IS NOT NULL AND "
    f"{_DURABLE_FACTS_PRESENT}) "
    "OR (status IN ('REJECTED', 'EXPIRED') AND active IS NULL "
    "AND completion_mode IS NULL AND intent_action IS NULL AND "
    f"{_DURABLE_FACTS_NULL}))"
)


def upgrade() -> None:
    op.create_table(
        "media_ingestion_reservations",
        sa.Column("reservation_id", sa.String(96), nullable=False),
        sa.Column("media_item_id", sa.String(96), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column(
            "session_id",
            sa.String(96),
            sa.ForeignKey(
                "learning_sessions.session_id",
                name="fk_media_ingestion_reservations_session",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canonical_artifact_id", sa.String(96)),
        sa.Column("evidence_id", sa.String(96)),
        sa.Column("intent_action", sa.String(32)),
        sa.Column("completion_mode", sa.String(32)),
        sa.Column("staged_object_key", sa.String(512)),
        sa.Column("staged_manifest_id", sa.String(255)),
        sa.Column("media_type", sa.String(128)),
        sa.Column("normalized_sha256", sa.String(64)),
        sa.Column("normalized_byte_size", sa.Integer()),
        sa.Column("learner_explanation", sa.Text()),
        sa.Column("attributes_json", sa.JSON(none_as_null=True)),
        sa.Column("result_json", sa.JSON(none_as_null=True)),
        sa.Column("rejection_reason", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "owner_id", "session_id", "idempotency_key", name="uq_media_ingestion_owner_session_key"
        ),
        sa.PrimaryKeyConstraint("reservation_id", name="pk_media_ingestion_reservations"),
        sa.CheckConstraint("slot >= 0", name="ck_media_ingestion_slot_nonnegative"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'PENDING_REFERENCE', 'COMPLETED', 'REJECTED', 'EXPIRED')",
            name="ck_media_ingestion_status",
        ),
        sa.CheckConstraint(
            "intent_action IS NULL OR intent_action IN ('MARK_REFERENCED', 'ABORT_STAGED')",
            name="ck_media_ingestion_intent_action",
        ),
        sa.CheckConstraint(
            "((status IN ('ACTIVE', 'PENDING_REFERENCE') AND active IS TRUE) "
            "OR (status IN ('COMPLETED', 'REJECTED', 'EXPIRED') AND active IS NULL))",
            name="ck_media_ingestion_status_active",
        ),
        sa.CheckConstraint(
            "((status = 'COMPLETED' AND ((completion_mode = 'ADOPTED' AND "
            "intent_action = 'MARK_REFERENCED') OR (completion_mode IN "
            "('FOLLOWER', 'DIRECT_REUSE') AND intent_action = 'ABORT_STAGED'))) "
            "OR (status <> 'COMPLETED' AND completion_mode IS NULL))",
            name="ck_media_ingestion_completion_mode",
        ),
        sa.CheckConstraint(
            _STATE_PAYLOAD_MATRIX,
            name="ck_media_ingestion_state_payload_matrix",
        ),
    )
    op.create_index(
        "ix_media_ingestion_owner_status_expires",
        "media_ingestion_reservations",
        ["owner_id", "status", "expires_at"],
    )
    op.create_index(
        "uq_media_ingestion_active_owner_slot",
        "media_ingestion_reservations",
        ["owner_id", "session_id", "slot"],
        unique=True,
        sqlite_where=sa.text("active IS TRUE"),
        postgresql_where=sa.text("active IS TRUE"),
    )
    op.create_table(
        "media_artifacts",
        sa.Column("media_item_id", sa.String(96), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column(
            "creator_reservation_id",
            sa.String(96),
            sa.ForeignKey(
                "media_ingestion_reservations.reservation_id",
                name="fk_media_artifacts_reservation",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("opaque_object_key", sa.String(512), nullable=False),
        sa.Column("manifest_id", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("normalized_sha256", sa.String(64), nullable=False),
        sa.Column("normalized_byte_size", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "owner_id", "normalized_sha256", name="uq_media_artifacts_owner_normalized_hash"
        ),
        sa.UniqueConstraint("opaque_object_key", name="uq_media_artifacts_object_key"),
        sa.PrimaryKeyConstraint("media_item_id", name="pk_media_artifacts"),
        sa.CheckConstraint(
            "state IN ('PENDING_REFERENCE', 'REFERENCED')",
            name="ck_media_artifacts_state",
        ),
        sa.CheckConstraint(
            "normalized_byte_size >= 0",
            name="ck_media_artifacts_byte_size_nonnegative",
        ),
    )
    op.create_index("ix_media_artifacts_owner_state", "media_artifacts", ["owner_id", "state"])
    with op.batch_alter_table("media_ingestion_reservations") as batch:
        batch.create_foreign_key(
            "fk_media_ingestion_canonical_artifact",
            "media_artifacts",
            ["canonical_artifact_id"],
            ["media_item_id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("evidence") as batch:
        batch.add_column(sa.Column("artifact_id", sa.String(96), nullable=True))
        batch.create_foreign_key(
            "fk_evidence_artifact",
            "media_artifacts",
            ["artifact_id"],
            ["media_item_id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_evidence_artifact_id", ["artifact_id"])


def downgrade() -> None:
    with op.batch_alter_table("evidence") as batch:
        batch.drop_index("ix_evidence_artifact_id")
        batch.drop_constraint("fk_evidence_artifact", type_="foreignkey")
        batch.drop_column("artifact_id")
    with op.batch_alter_table("media_ingestion_reservations") as batch:
        batch.drop_constraint("fk_media_ingestion_canonical_artifact", type_="foreignkey")
    op.drop_index("ix_media_artifacts_owner_state", table_name="media_artifacts")
    op.drop_table("media_artifacts")
    op.drop_index(
        "ix_media_ingestion_owner_status_expires", table_name="media_ingestion_reservations"
    )
    op.drop_index(
        "uq_media_ingestion_active_owner_slot",
        table_name="media_ingestion_reservations",
    )
    op.drop_table("media_ingestion_reservations")
