"""Drop obsolete Monad claim table.

Revision ID: 0007_drop_monad_evidence_claims
Revises: 0006_media_scan_receipts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0007_drop_monad_evidence_claims"
down_revision: str | None = "0006_media_scan_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "monad_evidence_claims"
_INDEX = "ix_monad_claim_session_evidence"


def _table_exists() -> bool:
    bind = op.get_bind()
    return _TABLE in sa.inspect(bind).get_table_names()


def _index_exists() -> bool:
    bind = op.get_bind()
    return any(item["name"] == _INDEX for item in sa.inspect(bind).get_indexes(_TABLE))


def upgrade() -> None:
    if context.is_offline_mode():
        op.drop_index(_INDEX, table_name=_TABLE)
        op.drop_table(_TABLE)
        return
    if not _table_exists():
        return
    if _index_exists():
        op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)


def downgrade() -> None:
    if context.is_offline_mode():
        _create_table()
        return
    if _table_exists():
        return
    _create_table()


def _create_table() -> None:
    op.create_table(
        _TABLE,
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
    op.create_index(_INDEX, _TABLE, ["session_id", "evidence_id"])
