"""Persist minimized security audit records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_security_audit_events"
down_revision: str | None = "0002_verified_principals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("request_id", sa.String(96), nullable=False),
        sa.Column("principal_id", sa.String(96)),
        sa.Column("token_fingerprint", sa.String(64)),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_category", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "request_id",
            name="uq_security_audit_events_request_id",
        ),
    )
    op.create_index(
        "ix_security_audit_events_occurred_at_id",
        "security_audit_events",
        ["occurred_at", "id"],
    )
    op.create_index(
        "ix_security_audit_events_principal_id",
        "security_audit_events",
        ["principal_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_security_audit_events_principal_id",
        table_name="security_audit_events",
    )
    op.drop_index(
        "ix_security_audit_events_occurred_at_id",
        table_name="security_audit_events",
    )
    op.drop_table("security_audit_events")
