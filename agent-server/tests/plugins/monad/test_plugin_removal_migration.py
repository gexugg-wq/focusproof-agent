from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect


def test_removing_claim_migration_preserves_core_tables(
    alembic_config: Config, database_url: str
) -> None:
    from focusproof.persistence.database import create_database_engine

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0003_security_audit_events")
    engine = create_database_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "monad_evidence_claims" not in tables
    assert {"learning_sessions", "evidence", "reviews"} <= tables
