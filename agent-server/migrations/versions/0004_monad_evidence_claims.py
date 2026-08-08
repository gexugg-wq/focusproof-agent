"""Prevent reuse of a Monad transaction across learning sessions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_monad_evidence_claims"
down_revision: str | None = "0003_security_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monad_evidence_claims",
        sa.Column("claim_id", sa.String(96), primary_key=True),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("transaction_hash", sa.String(66), nullable=False),
        sa.Column(
            "session_id",
            sa.String(96),
            sa.ForeignKey("learning_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            sa.String(96),
            sa.ForeignKey("evidence.evidence_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observation_event_id", sa.String(96), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "chain_id",
            "transaction_hash",
            name="uq_monad_claim_chain_transaction",
        ),
    )
    op.create_index(
        "ix_monad_claim_session_evidence",
        "monad_evidence_claims",
        ["session_id", "evidence_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_monad_claim_session_evidence", table_name="monad_evidence_claims"
    )
    op.drop_table("monad_evidence_claims")
