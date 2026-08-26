from __future__ import annotations

import ast
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Engine, inspect, insert, select, text
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
    "security_audit_events",
    "verified_principals",
    "media_ingestion_reservations",
    "media_artifacts",
}

_RESERVATION_DURABLE_FIELDS = (
    "canonical_artifact_id",
    "evidence_id",
    "staged_object_key",
    "staged_manifest_id",
    "media_type",
    "normalized_sha256",
    "normalized_byte_size",
    "learner_explanation",
    "attributes_json",
    "result_json",
)


def _media_reservation_values(status: str) -> dict[str, object]:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "reservation_id": f"res_{status.lower()}",
        "media_item_id": f"media_{status.lower()}",
        "owner_id": "owner",
        "session_id": "sess_matrix",
        "idempotency_key": f"key-{status.lower()}",
        "fingerprint": f"fp-{status.lower()}",
        "slot": 0,
        "status": status,
        "active": None,
        "expires_at": now,
        "canonical_artifact_id": None,
        "evidence_id": None,
        "intent_action": None,
        "completion_mode": None,
        "staged_object_key": None,
        "staged_manifest_id": None,
        "media_type": None,
        "normalized_sha256": None,
        "normalized_byte_size": None,
        "learner_explanation": None,
        "attributes_json": None,
        "result_json": None,
        "rejection_reason": None,
        "created_at": now,
        "updated_at": now,
    }
    if status == "ACTIVE":
        values["active"] = True
    elif status == "PENDING_REFERENCE":
        values.update(_pending_reservation_facts())
    elif status == "COMPLETED":
        values.update(_pending_reservation_facts())
        values.update(active=None, completion_mode="ADOPTED", intent_action="MARK_REFERENCED")
    return values


def _pending_reservation_facts() -> dict[str, object]:
    return {
        "active": True,
        "canonical_artifact_id": "media_canonical",
        "evidence_id": "ev_matrix",
        "intent_action": "MARK_REFERENCED",
        "staged_object_key": "opaque-staged-key",
        "staged_manifest_id": "manifest-staged",
        "media_type": "application/test",
        "normalized_sha256": "a" * 64,
        "normalized_byte_size": 7,
        "learner_explanation": "specific explanation",
        "attributes_json": "{}",
        "result_json": "{}",
    }


_INSERT_MEDIA_RESERVATION = text(
    """
    INSERT INTO media_ingestion_reservations (
        reservation_id, media_item_id, owner_id, session_id,
        idempotency_key, fingerprint, slot, status, active, expires_at,
        canonical_artifact_id, evidence_id, intent_action, completion_mode,
        staged_object_key, staged_manifest_id, media_type,
        normalized_sha256, normalized_byte_size, learner_explanation,
        attributes_json, result_json, rejection_reason, created_at, updated_at
    ) VALUES (
        :reservation_id, :media_item_id, :owner_id, :session_id,
        :idempotency_key, :fingerprint, :slot, :status, :active, :expires_at,
        :canonical_artifact_id, :evidence_id, :intent_action, :completion_mode,
        :staged_object_key, :staged_manifest_id, :media_type,
        :normalized_sha256, :normalized_byte_size, :learner_explanation,
        :attributes_json, :result_json, :rejection_reason, :created_at, :updated_at
    )
    """
)


def _insert_media_matrix_prerequisites(engine: Engine) -> None:
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO learning_sessions (
                    session_id, owner_user_id, status, adapter_mode, domain,
                    title, goal, conversation_id, runtime_mode, version,
                    created_at, updated_at
                ) VALUES (
                    'sess_matrix', 'owner', 'running', 'test', 'general',
                    'Matrix', 'Validate matrix',
                    '33333333-3333-3333-3333-333333333333', 'test', 1,
                    :now, :now
                )
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO media_artifacts (
                    media_item_id, owner_id, creator_reservation_id,
                    opaque_object_key, manifest_id, media_type,
                    normalized_sha256, normalized_byte_size, state, created_at
                ) VALUES (
                    'media_canonical', 'owner', NULL,
                    'opaque-canonical-key', 'manifest-canonical', 'application/test',
                    :digest, 7, 'PENDING_REFERENCE', :now
                )
                """
            ),
            {"digest": "a" * 64, "now": now},
        )


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
            text("SELECT review_result_json FROM learning_sessions WHERE session_id = 'sess_json'")
        ).scalar_one()
    assert "77" in str(payload)


def test_schema_compiles_for_postgresql() -> None:
    from focusproof.persistence.models import Base

    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    statements = [
        str(CreateTable(table).compile(dialect=dialect)) for table in Base.metadata.sorted_tables
    ]
    assert any("learning_sessions" in statement for statement in statements)
    assert any("source_openhands_event_id" in statement for statement in statements)
    assert any("security_audit_events" in statement for statement in statements)


def test_postgres_media_fixture_uses_alembic_without_metadata_ddl(
    project_root: Path,
) -> None:
    source_path = (
        project_root
        / "agent-server"
        / "tests"
        / "persistence"
        / "test_media_postgres_concurrency.py"
    )
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    metadata_ddl = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"create_all", "drop_all"}
    ]
    assert metadata_ddl == []

    fixture = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "postgres_media_factory"
    )
    calls = [node for node in ast.walk(fixture) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "command"
        and call.func.attr == "upgrade"
        and len(call.args) >= 2
        and isinstance(call.args[1], ast.Constant)
        and call.args[1].value == "head"
        for call in calls
    )
    assert any(
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "command"
        and call.func.attr == "downgrade"
        and len(call.args) >= 2
        and isinstance(call.args[1], ast.Constant)
        and call.args[1].value == "base"
        for call in calls
    )


def test_media_schema_constraint_contract(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    reservation_fks = {
        item["name"]: item for item in inspector.get_foreign_keys("media_ingestion_reservations")
    }
    artifact_fks = {item["name"]: item for item in inspector.get_foreign_keys("media_artifacts")}
    evidence_fks = {item["name"]: item for item in inspector.get_foreign_keys("evidence")}
    assert (
        reservation_fks["fk_media_ingestion_reservations_session"]["options"]["ondelete"]
        == "CASCADE"
    )
    assert (
        reservation_fks["fk_media_ingestion_canonical_artifact"]["options"]["ondelete"]
        == "SET NULL"
    )
    assert artifact_fks["fk_media_artifacts_reservation"]["options"]["ondelete"] == ("SET NULL")
    assert evidence_fks["fk_evidence_artifact"]["options"]["ondelete"] == "RESTRICT"
    unique_names = {item["name"] for item in inspector.get_unique_constraints("media_artifacts")}
    assert {
        "uq_media_artifacts_owner_normalized_hash",
        "uq_media_artifacts_object_key",
    } <= unique_names
    index_names = {item["name"] for item in inspector.get_indexes("media_ingestion_reservations")}
    assert "uq_media_ingestion_active_owner_slot" in index_names
    check_names = {
        item["name"] for item in inspector.get_check_constraints("media_ingestion_reservations")
    }
    assert {
        "ck_media_ingestion_slot_nonnegative",
        "ck_media_ingestion_status_active",
        "ck_media_ingestion_completion_mode",
        "ck_media_ingestion_state_payload_matrix",
    } <= check_names


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("completion_mode", "ADOPTED"),
        ("intent_action", "MARK_REFERENCED"),
        ("canonical_artifact_id", "media_canonical"),
        ("evidence_id", "ev_illegal"),
        ("staged_object_key", "opaque-illegal"),
        ("staged_manifest_id", "manifest-illegal"),
        ("media_type", "application/test"),
        ("normalized_sha256", "b" * 64),
        ("normalized_byte_size", 7),
        ("learner_explanation", "not allowed while active"),
        ("attributes_json", "{}"),
        ("result_json", "{}"),
    ],
)
def test_active_reservation_rejects_durable_finalize_facts(
    migrated_engine: Engine,
    field: str,
    value: object,
) -> None:
    _insert_media_matrix_prerequisites(migrated_engine)
    values = _media_reservation_values("ACTIVE")
    values[field] = value
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(_INSERT_MEDIA_RESERVATION, values)


@pytest.mark.parametrize(
    "missing_field",
    ["intent_action", *_RESERVATION_DURABLE_FIELDS],
)
def test_pending_reservation_requires_complete_durable_facts(
    migrated_engine: Engine,
    missing_field: str,
) -> None:
    _insert_media_matrix_prerequisites(migrated_engine)
    values = _media_reservation_values("PENDING_REFERENCE")
    values[missing_field] = None
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(_INSERT_MEDIA_RESERVATION, values)


def test_pending_reservation_rejects_explicit_json_null_result(
    migrated_engine: Engine,
) -> None:
    from focusproof.persistence.models import MediaIngestionReservationModel

    _insert_media_matrix_prerequisites(migrated_engine)
    values = _media_reservation_values("PENDING_REFERENCE")
    values["result_json"] = JSON.NULL
    with pytest.raises(IntegrityError) as caught, migrated_engine.begin() as connection:
        connection.execute(insert(MediaIngestionReservationModel), values)
    assert "ck_media_ingestion_state_payload_matrix" in str(caught.value.orig)


def test_completed_reservation_rejects_explicit_json_null_attributes(
    migrated_engine: Engine,
) -> None:
    from focusproof.persistence.models import MediaIngestionReservationModel

    _insert_media_matrix_prerequisites(migrated_engine)
    values = _media_reservation_values("COMPLETED")
    values["attributes_json"] = JSON.NULL
    with pytest.raises(IntegrityError) as caught, migrated_engine.begin() as connection:
        connection.execute(insert(MediaIngestionReservationModel), values)
    assert "ck_media_ingestion_state_payload_matrix" in str(caught.value.orig)


def test_python_none_state_json_is_stored_as_sql_null(
    migrated_engine: Engine,
) -> None:
    from focusproof.persistence.models import MediaIngestionReservationModel

    _insert_media_matrix_prerequisites(migrated_engine)
    values = _media_reservation_values("ACTIVE")
    with migrated_engine.begin() as connection:
        connection.execute(insert(MediaIngestionReservationModel), values)
        stored_nulls = connection.execute(
            select(
                MediaIngestionReservationModel.attributes_json.is_(None),
                MediaIngestionReservationModel.result_json.is_(None),
            ).where(MediaIngestionReservationModel.reservation_id == values["reservation_id"])
        ).one()
    assert stored_nulls == (True, True)


@pytest.mark.parametrize("status", ["ACTIVE", "REJECTED", "EXPIRED"])
@pytest.mark.parametrize("json_field", ["attributes_json", "result_json"])
def test_states_without_durable_payload_reject_explicit_json_null(
    migrated_engine: Engine,
    status: str,
    json_field: str,
) -> None:
    from focusproof.persistence.models import MediaIngestionReservationModel

    _insert_media_matrix_prerequisites(migrated_engine)
    values = _media_reservation_values(status)
    values[json_field] = JSON.NULL
    with pytest.raises(IntegrityError) as caught, migrated_engine.begin() as connection:
        connection.execute(insert(MediaIngestionReservationModel), values)
    assert "ck_media_ingestion_state_payload_matrix" in str(caught.value.orig)


@pytest.mark.parametrize(
    ("completion_mode", "intent_action"),
    [
        ("ADOPTED", "MARK_REFERENCED"),
        ("FOLLOWER", "ABORT_STAGED"),
        ("DIRECT_REUSE", "ABORT_STAGED"),
    ],
)
def test_completed_modes_accept_empty_json_objects(
    migrated_engine: Engine,
    completion_mode: str,
    intent_action: str,
) -> None:
    from focusproof.persistence.models import MediaIngestionReservationModel

    _insert_media_matrix_prerequisites(migrated_engine)
    values = _media_reservation_values("COMPLETED")
    values.update(
        attributes_json={},
        result_json={},
        completion_mode=completion_mode,
        intent_action=intent_action,
    )
    with migrated_engine.begin() as connection:
        connection.execute(insert(MediaIngestionReservationModel), values)
        stored_payloads = connection.execute(
            select(
                MediaIngestionReservationModel.attributes_json,
                MediaIngestionReservationModel.result_json,
            ).where(MediaIngestionReservationModel.reservation_id == values["reservation_id"])
        ).one()
    assert stored_payloads == ({}, {})


def test_pending_reservation_rejects_completion_mode_and_negative_size(
    migrated_engine: Engine,
) -> None:
    _insert_media_matrix_prerequisites(migrated_engine)
    for field, value in (("completion_mode", "ADOPTED"), ("normalized_byte_size", -1)):
        values = _media_reservation_values("PENDING_REFERENCE")
        values[field] = value
        with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
            connection.execute(_INSERT_MEDIA_RESERVATION, values)


@pytest.mark.parametrize(
    "missing_field",
    ["completion_mode", "intent_action", *_RESERVATION_DURABLE_FIELDS],
)
def test_completed_reservation_requires_complete_durable_facts(
    migrated_engine: Engine,
    missing_field: str,
) -> None:
    _insert_media_matrix_prerequisites(migrated_engine)
    values = _media_reservation_values("COMPLETED")
    values[missing_field] = None
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(_INSERT_MEDIA_RESERVATION, values)


@pytest.mark.parametrize(
    ("completion_mode", "intent_action"),
    [
        ("ADOPTED", "ABORT_STAGED"),
        ("FOLLOWER", "MARK_REFERENCED"),
        ("DIRECT_REUSE", "MARK_REFERENCED"),
        ("UNKNOWN", "ABORT_STAGED"),
    ],
)
def test_completed_reservation_rejects_illegal_mode_intent_pair(
    migrated_engine: Engine,
    completion_mode: str,
    intent_action: str,
) -> None:
    _insert_media_matrix_prerequisites(migrated_engine)
    values = _media_reservation_values("COMPLETED")
    values.update(completion_mode=completion_mode, intent_action=intent_action)
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(_INSERT_MEDIA_RESERVATION, values)


@pytest.mark.parametrize("status", ["REJECTED", "EXPIRED"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("completion_mode", "ADOPTED"),
        ("intent_action", "MARK_REFERENCED"),
        ("canonical_artifact_id", "media_canonical"),
        ("evidence_id", "ev_illegal"),
        ("staged_object_key", "opaque-illegal"),
        ("staged_manifest_id", "manifest-illegal"),
        ("media_type", "application/test"),
        ("normalized_sha256", "b" * 64),
        ("normalized_byte_size", 7),
        ("learner_explanation", "not allowed when terminal"),
        ("attributes_json", "{}"),
        ("result_json", "{}"),
    ],
)
def test_noncompleted_terminal_reservation_rejects_finalize_facts(
    migrated_engine: Engine,
    status: str,
    field: str,
    value: object,
) -> None:
    _insert_media_matrix_prerequisites(migrated_engine)
    values = _media_reservation_values(status)
    values[field] = value
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(_INSERT_MEDIA_RESERVATION, values)


def test_active_to_pending_update_requires_complete_durable_facts(
    migrated_engine: Engine,
) -> None:
    _insert_media_matrix_prerequisites(migrated_engine)
    with migrated_engine.begin() as connection:
        connection.execute(_INSERT_MEDIA_RESERVATION, _media_reservation_values("ACTIVE"))
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE media_ingestion_reservations
                SET status = 'PENDING_REFERENCE', active = TRUE
                WHERE reservation_id = 'res_active'
                """
            )
        )


def _override_config(project_root: Path, default_url: str, raw_x: list[str]) -> Config:
    config = Config()
    config.set_main_option("script_location", str(project_root / "agent-server" / "migrations"))
    config.set_main_option("sqlalchemy.url", default_url)
    config.cmd_opts = Namespace(x=raw_x)
    return config


def test_database_url_override_changes_only_override_database(
    project_root: Path, tmp_path: Path
) -> None:
    default_path = tmp_path / "default.sqlite3"
    override_path = tmp_path / "override.sqlite3"
    default_path.write_bytes(b"must remain untouched")
    before_bytes = default_path.read_bytes()
    before_mtime = default_path.stat().st_mtime_ns
    config = _override_config(
        project_root,
        f"sqlite+pysqlite:///{default_path}",
        [f"database_url=sqlite+pysqlite:///{override_path}"],
    )
    command.upgrade(config, "head")
    assert override_path.exists()
    assert default_path.read_bytes() == before_bytes
    assert default_path.stat().st_mtime_ns == before_mtime


def test_omitted_database_url_override_uses_configured_database(
    project_root: Path, tmp_path: Path
) -> None:
    default_path = tmp_path / "default.sqlite3"
    config = _override_config(project_root, f"sqlite+pysqlite:///{default_path}", [])
    command.upgrade(config, "head")
    assert default_path.exists()


@pytest.mark.parametrize(
    "raw_x",
    [
        ["database_url="],
        ["unknown=sqlite+pysqlite:////tmp/unused.sqlite3"],
        [
            "database_url=sqlite+pysqlite:////tmp/a.sqlite3",
            "database_url=sqlite+pysqlite:////tmp/b.sqlite3",
        ],
        ["database_url=://secret-user:secret-pass@invalid?token=query-secret"],
    ],
)
def test_database_url_override_fails_closed_without_secret_output(
    project_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    raw_x: list[str],
) -> None:
    config = _override_config(
        project_root, f"sqlite+pysqlite:///{tmp_path / 'default.sqlite3'}", raw_x
    )
    with pytest.raises(ValueError) as caught:
        command.upgrade(config, "head")
    output = capsys.readouterr()
    combined = f"{caught.value} {output.out} {output.err}"
    assert "secret-user" not in combined
    assert "secret-pass" not in combined
    assert "query-secret" not in combined


def test_remove_monad_forward_migration_drops_claim_table_and_preserves_generic_data(
    alembic_config: Config,
    database_url: str,
) -> None:
    from focusproof.persistence.database import create_database_engine

    command.upgrade(alembic_config, "0006_media_scan_receipts")
    engine = create_database_engine(database_url)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO learning_sessions (
                    session_id, owner_user_id, status, adapter_mode, domain,
                    title, goal, conversation_id, runtime_mode, version,
                    created_at, updated_at
                ) VALUES (
                    :session_id, :owner_user_id, :status, :adapter_mode, :domain,
                    :title, :goal, :conversation_id, :runtime_mode, :version,
                    :created_at, :updated_at
                )
                """
            ),
            {
                "session_id": "sess_remove_monad",
                "owner_user_id": "owner",
                "status": "running",
                "adapter_mode": "test",
                "domain": "general",
                "title": "Generic",
                "goal": "Preserve data",
                "conversation_id": "44444444-4444-4444-4444-444444444444",
                "runtime_mode": "test",
                "version": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO evidence (
                    evidence_id, session_id, evidence_type, content_hash,
                    text_content, metadata_json, created_at
                ) VALUES (
                    :evidence_id, :session_id, :evidence_type, :content_hash,
                    :text_content, :metadata_json, :created_at
                )
                """
            ),
            {
                "evidence_id": "ev_remove_monad",
                "session_id": "sess_remove_monad",
                "evidence_type": "text",
                "content_hash": "sha256:remove-monad",
                "text_content": "Generic evidence remains",
                "metadata_json": "{}",
                "created_at": now,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO monad_evidence_claims (
                    claim_id, chain_id, transaction_hash, session_id,
                    evidence_id, observation_event_id, created_at
                ) VALUES (
                    :claim_id, :chain_id, :transaction_hash, :session_id,
                    :evidence_id, :observation_event_id, :created_at
                )
                """
            ),
            {
                "claim_id": "claim_remove_monad",
                "chain_id": 1234,
                "transaction_hash": "0x" + "ab" * 32,
                "session_id": "sess_remove_monad",
                "evidence_id": "ev_remove_monad",
                "observation_event_id": "obs_remove_monad",
                "created_at": now,
            },
        )
    engine.dispose()

    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert "monad_evidence_claims" not in tables
        assert (
            connection.execute(
                text("SELECT count(*) FROM learning_sessions WHERE session_id = :session_id"),
                {"session_id": "sess_remove_monad"},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM evidence WHERE evidence_id = :evidence_id"),
                {"evidence_id": "ev_remove_monad"},
            ).scalar_one()
            == 1
        )
    engine.dispose()

    command.downgrade(alembic_config, "0006_media_scan_receipts")
    engine = create_database_engine(database_url)
    assert "monad_evidence_claims" in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(alembic_config, "head")
    engine = create_database_engine(database_url)
    assert "monad_evidence_claims" not in inspect(engine).get_table_names()
    engine.dispose()
