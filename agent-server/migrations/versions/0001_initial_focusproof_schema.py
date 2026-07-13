"""Initial FocusProof persistence schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_focusproof_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_sessions",
        sa.Column("session_id", sa.String(96), primary_key=True),
        sa.Column("owner_user_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("adapter_mode", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(128), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text()),
        sa.Column("planned_minutes", sa.Integer()),
        sa.Column("conversation_id", sa.String(36), nullable=False, unique=True),
        sa.Column("runtime_mode", sa.String(64), nullable=False),
        sa.Column("review_result_json", sa.JSON()),
        sa.Column("goal_conversation_synced_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_learning_sessions_owner_user_id",
        "learning_sessions",
        ["owner_user_id"],
    )
    op.create_table(
        "evidence",
        sa.Column("evidence_id", sa.String(96), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(96),
            sa.ForeignKey("learning_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_type", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("text_content", sa.Text()),
        sa.Column("source_url", sa.Text()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("conversation_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "evidence_id"),
    )
    op.create_index(
        "ix_evidence_session_content_hash",
        "evidence",
        ["session_id", "content_hash"],
    )
    op.create_table(
        "learner_answers",
        sa.Column(
            "session_id",
            sa.String(96),
            sa.ForeignKey("learning_sessions.session_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("question_id", sa.String(128), primary_key=True),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("conversation_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(96), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(96),
            sa.ForeignKey("learning_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("source_openhands_event_id", sa.String(96)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "sequence"),
        sa.UniqueConstraint("session_id", "source_openhands_event_id"),
    )
    op.create_table(
        "reviews",
        sa.Column("review_id", sa.String(96), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(96),
            sa.ForeignKey("learning_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("review_status", sa.String(64), nullable=False),
        sa.Column("score", sa.Integer()),
        sa.Column("result_json", sa.JSON()),
        sa.Column("native_event_count", sa.Integer(), nullable=False),
        sa.Column("source_openhands_event_id", sa.String(96)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "source_openhands_event_id"),
    )


def downgrade() -> None:
    op.drop_table("reviews")
    op.drop_table("audit_events")
    op.drop_table("learner_answers")
    op.drop_index("ix_evidence_session_content_hash", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index(
        "ix_learning_sessions_owner_user_id", table_name="learning_sessions"
    )
    op.drop_table("learning_sessions")
