from __future__ import annotations

from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

EXPECTED_TABLES = {
    "alembic_version",
    "audit_events",
    "evidence",
    "learner_answers",
    "learning_sessions",
    "reviews",
}


def test_upgrade_downgrade_and_reupgrade(
    alembic_config: Config,
    database_url: str,
) -> None:
    from focusproof.persistence.database import create_database_engine

    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(alembic_config, "base")
    engine = create_database_engine(database_url)
    assert "learning_sessions" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_sqlite_foreign_keys_are_enforced(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO evidence (
                        evidence_id, session_id, evidence_type, content_hash,
                        metadata_json, created_at
                    ) VALUES (
                        'ev_orphan', 'sess_missing', 'text', 'sha256:missing',
                        '{}', :created_at
                    )
                    """
                ),
                {"created_at": datetime.now(UTC)},
            )


def test_native_source_unique_constraints_are_enforced(
    migrated_engine: Engine,
) -> None:
    now = datetime.now(UTC)
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO learning_sessions (
                    session_id, owner_user_id, status, adapter_mode, domain,
                    title, goal, conversation_id, runtime_mode, version,
                    created_at, updated_at
                ) VALUES (
                    'sess_1', 'dev-anonymous-user', 'running',
                    'openhands-local-scripted-test', 'general', 'Replay',
                    'Explain replay', '11111111-1111-1111-1111-111111111111',
                    'openhands-local-scripted-test', 1, :now, :now
                )
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO reviews (
                    review_id, session_id, conversation_id, review_status,
                    native_event_count, source_openhands_event_id, created_at
                ) VALUES (
                    'rev_1', 'sess_1',
                    '11111111-1111-1111-1111-111111111111', 'awaiting_user',
                    3, 'native_review_1', :now
                )
                """
            ),
            {"now": now},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO reviews (
                        review_id, session_id, conversation_id, review_status,
                        native_event_count, source_openhands_event_id, created_at
                    ) VALUES (
                        'rev_2', 'sess_1',
                        '11111111-1111-1111-1111-111111111111', 'awaiting_user',
                        3, 'native_review_1', :now
                    )
                    """
                ),
                {"now": now},
            )


def test_json_fields_round_trip(migrated_engine: Engine) -> None:
    now = datetime.now(UTC)
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO learning_sessions (
                    session_id, owner_user_id, status, adapter_mode, domain,
                    title, goal, conversation_id, runtime_mode,
                    review_result_json, version, created_at, updated_at
                ) VALUES (
                    'sess_json', 'dev-anonymous-user', 'reviewed',
                    'openhands-local-scripted-test', 'general', 'JSON', 'Store JSON',
                    '22222222-2222-2222-2222-222222222222',
                    'openhands-local-scripted-test', :payload, 1, :now, :now
                )
                """
            ),
            {"payload": '{"score": 77}', "now": now},
        )
        payload = connection.execute(
            text(
                "SELECT review_result_json FROM learning_sessions "
                "WHERE session_id = 'sess_json'"
            )
        ).scalar_one()
    assert "77" in str(payload)


def test_schema_compiles_for_postgresql() -> None:
    from focusproof.persistence.models import Base

    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    statements = [
        str(CreateTable(table).compile(dialect=dialect))
        for table in Base.metadata.sorted_tables
    ]
    assert any("learning_sessions" in statement for statement in statements)
    assert any("source_openhands_event_id" in statement for statement in statements)
