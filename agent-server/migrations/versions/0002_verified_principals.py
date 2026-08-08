"""Persist opaque verified OIDC principals."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_verified_principals"
down_revision: str | None = "0001_initial_focusproof_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "verified_principals",
        sa.Column("principal_id", sa.String(96), primary_key=True),
        sa.Column("issuer", sa.String(2048), nullable=False),
        sa.Column("subject", sa.String(1024), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_verified_principals_issuer_subject",
        ),
    )


def downgrade() -> None:
    op.drop_table("verified_principals")
