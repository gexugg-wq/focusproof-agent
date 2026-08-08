from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


def test_upgrade_downgrade_reupgrade_claim_table(alembic_config: Config, database_url: str) -> None:
    from focusproof.persistence.database import create_database_engine

    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    assert "monad_evidence_claims" in inspect(engine).get_table_names()
    engine.dispose()
    command.downgrade(alembic_config, "0003_security_audit_events")
    engine = create_database_engine(database_url)
    assert "monad_evidence_claims" not in inspect(engine).get_table_names()
    engine.dispose()
    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    assert "monad_evidence_claims" in inspect(engine).get_table_names()
    engine.dispose()


def test_claim_table_compiles_for_postgresql() -> None:
    from focusproof.persistence.models import MonadEvidenceClaimModel

    ddl = str(CreateTable(MonadEvidenceClaimModel.__table__).compile(
        dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
    assert "UNIQUE (chain_id, transaction_hash)" in ddl
    assert "monad_evidence_claims" in ddl
