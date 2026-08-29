from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

SPEECH_TABLES = {"speech_transcription_requests", "speech_resource_slots"}
FORBIDDEN_COLUMN_PARTS = {"audio", "transcript", "payload", "path", "blob", "body", "secret"}


def test_speech_migration_upgrade_downgrade_reupgrade(
    alembic_config: Config, database_url: str
) -> None:
    from focusproof.persistence.database import create_database_engine

    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    assert SPEECH_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()
    command.downgrade(alembic_config, "0007_drop_monad_evidence_claims")
    engine = create_database_engine(database_url)
    assert SPEECH_TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()
    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    assert SPEECH_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_speech_schema_is_metadata_only(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    request_columns = {
        item["name"]
        for item in inspector.get_columns("speech_transcription_requests")
    }
    slot_columns = {
        item["name"] for item in inspector.get_columns("speech_resource_slots")
    }
    assert request_columns == {
        "request_id", "session_id", "owner_user_id", "idempotency_key_hash",
        "hmac_key_version", "request_fingerprint", "state", "media_type",
        "byte_size", "duration_ms", "provider", "model", "provider_attempts",
        "lease_owner", "lease_generation", "lease_expires_at",
        "provider_dispatched_at", "outcome_code", "latency_ms", "created_at",
        "updated_at", "completed_at",
    }
    assert slot_columns == {
        "resource_kind", "slot_number", "lease_owner_token", "work_kind",
        "work_id", "config_generation", "enabled", "lease_generation",
        "lease_expires_at",
    }
    assert not {
        column for column in request_columns | slot_columns
        if any(part in column.lower() for part in FORBIDDEN_COLUMN_PARTS)
    }


def test_request_state_check_matrix(migrated_engine: Engine) -> None:
    now = datetime.now(UTC)
    with migrated_engine.begin() as connection:
        _insert_session(connection, now)
    invalid = (
        _request_values("active-without-lease", lease_owner=None),
        _request_values(
            "terminal-with-lease", state="cancelled", completed_at=now,
            outcome_code="client_cancelled",
        ),
        _request_values("dispatching-without-time", state="dispatching"),
        _request_values(
            "succeeded-with-outcome", state="succeeded", lease_owner=None,
            lease_expires_at=None, completed_at=now, provider_dispatched_at=now,
            provider_attempts=1, outcome_code="provider_error", latency_ms=1,
        ),
    )
    for values in invalid:
        with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
            connection.execute(_INSERT_REQUEST, values)


def test_resource_slot_check_matrix(migrated_engine: Engine) -> None:
    now = datetime.now(UTC)
    invalid = (
        {
            "resource_kind": "asr", "slot_number": 1, "lease_owner_token": None,
            "work_kind": None, "work_id": None, "config_generation": 1,
            "enabled": True, "lease_generation": 0, "lease_expires_at": now,
        },
        {
            "resource_kind": "asr", "slot_number": 2, "lease_owner_token": "opaque",
            "work_kind": None, "work_id": "work", "config_generation": 1,
            "enabled": True, "lease_generation": 1,
            "lease_expires_at": now + timedelta(minutes=1),
        },
    )
    for values in invalid:
        with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
            connection.execute(_INSERT_SLOT, values)



def test_hmac_hash_is_unique_within_owner_session_and_version(
    migrated_engine: Engine,
) -> None:
    now = datetime.now(UTC)
    with migrated_engine.begin() as connection:
        _insert_session(connection, now)
        connection.execute(
            _INSERT_REQUEST,
            _request_values(
                "unique-one",
                request_id="00000000-0000-0000-0000-000000000001",
            ),
        )
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(
            _INSERT_REQUEST,
            _request_values(
                "unique-two",
                request_id="00000000-0000-0000-0000-000000000002",
            ),
        )


def _insert_session(connection: object, now: datetime) -> None:
    connection.execute(
        text(
            """
            INSERT INTO learning_sessions (
                session_id, owner_user_id, status, adapter_mode, domain, title, goal,
                conversation_id, runtime_mode, version, created_at, updated_at
            ) VALUES (
                'sess-matrix', 'owner', 'running', 'test', 'general', 'Speech', 'Test',
                '77777777-7777-7777-7777-777777777777', 'test', 1, :now, :now
            )
            """
        ), {"now": now},
    )


def _request_values(name: str, **overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "request_id": f"00000000-0000-0000-0000-{len(name):012d}",
        "session_id": "sess-matrix", "owner_user_id": "owner",
        "idempotency_key_hash": "a" * 64, "hmac_key_version": "v1",
        "request_fingerprint": "b" * 64, "state": "admitted",
        "media_type": None, "byte_size": None, "duration_ms": None,
        "provider": "dashscope", "model": "qwen3-asr-flash",
        "provider_attempts": 0, "lease_owner": "worker", "lease_generation": 1,
        "lease_expires_at": now + timedelta(minutes=1),
        "provider_dispatched_at": None, "outcome_code": None, "latency_ms": None,
        "created_at": now, "updated_at": now, "completed_at": None,
    }
    values.update(overrides)
    return values


_INSERT_REQUEST = text("""
INSERT INTO speech_transcription_requests (
 request_id, session_id, owner_user_id, idempotency_key_hash, hmac_key_version,
 request_fingerprint, state, media_type, byte_size, duration_ms, provider, model,
 provider_attempts, lease_owner, lease_generation, lease_expires_at,
 provider_dispatched_at, outcome_code, latency_ms, created_at, updated_at, completed_at
) VALUES (
 :request_id, :session_id, :owner_user_id, :idempotency_key_hash, :hmac_key_version,
 :request_fingerprint, :state, :media_type, :byte_size, :duration_ms, :provider, :model,
 :provider_attempts, :lease_owner, :lease_generation, :lease_expires_at,
 :provider_dispatched_at, :outcome_code, :latency_ms, :created_at, :updated_at, :completed_at
)""")

_INSERT_SLOT = text("""
INSERT INTO speech_resource_slots (
 resource_kind, slot_number, lease_owner_token, work_kind, work_id,
 config_generation, enabled, lease_generation, lease_expires_at
) VALUES (
 :resource_kind, :slot_number, :lease_owner_token, :work_kind, :work_id,
 :config_generation, :enabled, :lease_generation, :lease_expires_at
)""")
