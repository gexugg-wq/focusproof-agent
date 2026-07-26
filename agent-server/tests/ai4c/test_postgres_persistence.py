from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import os
from pathlib import Path
from threading import Barrier
from time import monotonic, sleep
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from focusproof.persistence.database import create_database_engine, create_session_factory
from focusproof.persistence.repositories import (
    SqlAuditEventRepository,
    StoredAuditEvent,
    StoredEvidence,
    StoredReview,
    StoredSession,
)
from focusproof.persistence.unit_of_work import UnitOfWorkFactory

pytestmark = pytest.mark.postgres

PGHOST = "127.0.0.1"
PGPORT = 5432
PGDATABASE = "focusproof_ai4c_task3"
PGUSER = "focusproof_ai4c"
TASK3_PGPASSFILE = "/tmp/focusproof-ai4c-task3.pgpass"

EXPECTED_TABLES = {
    "alembic_version",
    "audit_events",
    "evidence",
    "learner_answers",
    "learning_sessions",
    "reviews",
    "security_audit_events",
    "verified_principals",
}


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture
def postgres_schema() -> Iterator[str]:
    _require_task3_pgpass()
    schema = f"ai4c_{uuid4().hex}"
    admin_engine = create_engine(_base_url(), pool_pre_ping=True, poolclass=NullPool)
    try:
        with admin_engine.begin() as connection:
            row = connection.execute(
                text("select current_database(), current_user")
            ).one()
            if str(row[0]) != PGDATABASE or str(row[1]) != PGUSER:
                pytest.fail("postgres_target_mismatch")
            connection.execute(
                text(
                    f"CREATE SCHEMA {_quote_identifier(schema)} "
                    f"AUTHORIZATION {_quote_identifier(PGUSER)}"
                )
            )
        yield schema
    finally:
        with admin_engine.begin() as connection:
            connection.execute(
                text(f"DROP SCHEMA IF EXISTS {_quote_identifier(schema)} CASCADE")
            )
        admin_engine.dispose()


@pytest.fixture
def postgres_database_url(postgres_schema: str) -> str:
    return _schema_url(postgres_schema)


@pytest.fixture
def postgres_alembic_config(project_root: Path, postgres_database_url: str) -> Config:
    config = Config(project_root / "alembic.ini")
    config.set_main_option("script_location", str(project_root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", postgres_database_url.replace("%", "%%"))
    return config


@pytest.fixture
def migrated_postgres_engine(
    postgres_alembic_config: Config,
    postgres_database_url: str,
) -> Iterator[Engine]:
    command.upgrade(postgres_alembic_config, "head")
    engine = create_database_engine(postgres_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def postgres_uow_factory(migrated_postgres_engine: Engine) -> UnitOfWorkFactory:
    return UnitOfWorkFactory(create_session_factory(migrated_postgres_engine))


def test_postgres_migrations_upgrade_downgrade_reupgrade_constraints_and_types(
    postgres_alembic_config: Config,
    postgres_database_url: str,
) -> None:
    command.upgrade(postgres_alembic_config, "head")
    engine = create_database_engine(postgres_database_url)
    try:
        assert EXPECTED_TABLES <= _table_names(engine)
        assert ("session_id", "sequence") in _unique_columns(engine, "audit_events")
        assert ("session_id", "source_openhands_event_id") in _unique_columns(
            engine, "audit_events"
        )
        assert ("session_id", "source_openhands_event_id") in _unique_columns(
            engine, "reviews"
        )
        assert {
            "ix_learning_sessions_owner_user_id",
            "ix_evidence_session_content_hash",
            "ix_security_audit_events_occurred_at_id",
            "ix_security_audit_events_principal_id",
        } <= _index_names(engine)

        factory = UnitOfWorkFactory(create_session_factory(engine))
        payload = {"score": 88, "flags": ["postgres", "json"]}
        session = _session("sess_pg_types").model_copy(
            update={"review_result": payload}
        )
        evidence = _evidence("sess_pg_types", "ev_pg_types")
        with factory() as uow:
            uow.sessions.create(session)
            uow.evidence.add(evidence)
            uow.commit()

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "select review_result_json, created_at, conversation_id "
                    "from learning_sessions where session_id = :session_id"
                ),
                {"session_id": "sess_pg_types"},
            ).mappings().one()
            evidence_row = connection.execute(
                text(
                    "select metadata_json from evidence "
                    "where session_id = :session_id and evidence_id = :evidence_id"
                ),
                {"session_id": "sess_pg_types", "evidence_id": "ev_pg_types"},
            ).mappings().one()

        created_at = cast(datetime, row["created_at"])
        assert row["review_result_json"] == payload
        assert evidence_row["metadata_json"] == {"source": "postgres", "nested": True}
        assert created_at.tzinfo is not None
        assert UUID(str(row["conversation_id"])) == UUID(session.conversation_id)
    finally:
        engine.dispose()

    command.downgrade(postgres_alembic_config, "base")
    downgraded_engine = create_database_engine(postgres_database_url)
    try:
        assert "learning_sessions" not in _table_names(downgraded_engine)
    finally:
        downgraded_engine.dispose()

    command.upgrade(postgres_alembic_config, "head")
    reupgraded_engine = create_database_engine(postgres_database_url)
    try:
        assert EXPECTED_TABLES <= _table_names(reupgraded_engine)
    finally:
        reupgraded_engine.dispose()


def test_postgres_uow_rollback_owner_isolation_and_reviewed_sessions_are_terminal(
    postgres_uow_factory: UnitOfWorkFactory,
) -> None:
    with pytest.raises(RuntimeError, match="abort"):
        with postgres_uow_factory() as uow:
            uow.sessions.create(_session("sess_rollback", owner_user_id="owner_a"))
            raise RuntimeError("abort")

    with postgres_uow_factory() as uow:
        assert uow.sessions.get("sess_rollback") is None

    owner_a_session = _session("sess_owner_a", owner_user_id="owner_a")
    owner_b_session = _session("sess_owner_b", owner_user_id="owner_b")
    with postgres_uow_factory() as uow:
        uow.sessions.create(owner_a_session)
        uow.sessions.create(owner_b_session)
        uow.commit()

    with postgres_uow_factory() as uow:
        assert uow.sessions.get_owned("sess_owner_a", "owner_a") is not None
        assert uow.sessions.get_owned("sess_owner_a", "owner_b") is None
        assert uow.sessions.get_owned("sess_owner_b", "owner_a") is None

    with postgres_uow_factory() as uow:
        completed = uow.reviews.add_from_native_event(
            _review(
                "rev_owner_a",
                owner_a_session,
                review_status="completed",
                source_openhands_event_id="native_review_owner_a",
            )
        )
        uow.commit()

    assert completed.review_id == "rev_owner_a"
    with postgres_uow_factory() as uow:
        reviewed = uow.sessions.get("sess_owner_a")
        assert reviewed is not None
        assert reviewed.status == "reviewed"
        changed = uow.sessions.update_status(
            "sess_owner_a",
            "running",
            expected_version=reviewed.version,
        )
        uow.commit()

    assert changed is False
    with postgres_uow_factory() as uow:
        still_reviewed = uow.sessions.get("sess_owner_a")
    assert still_reviewed is not None
    assert still_reviewed.status == "reviewed"


def test_postgres_native_reference_ids_survive_engine_restart(
    postgres_alembic_config: Config,
    postgres_database_url: str,
) -> None:
    command.upgrade(postgres_alembic_config, "head")
    first_engine = create_database_engine(postgres_database_url)
    session = _session("sess_restart", owner_user_id="owner_restart")
    evidence = _evidence("sess_restart", "ev_restart")
    try:
        factory = UnitOfWorkFactory(create_session_factory(first_engine))
        with factory() as uow:
            uow.sessions.create(session)
            uow.evidence.add(evidence)
            audit = uow.audit_events.append(
                "sess_restart",
                "verification.completed",
                "tool",
                {"verified": True, "source": "native"},
                source_openhands_event_id="native_audit_restart",
                event_id="evt_restart",
            )
            review = uow.reviews.add_from_native_event(
                _review(
                    "rev_restart",
                    session,
                    review_status="completed",
                    source_openhands_event_id="native_review_restart",
                )
            )
            uow.commit()
    finally:
        first_engine.dispose()

    restarted_engine = create_database_engine(postgres_database_url)
    try:
        restarted_factory = UnitOfWorkFactory(create_session_factory(restarted_engine))
        with restarted_factory() as uow:
            stored_session = uow.sessions.get("sess_restart")
            stored_evidence = uow.evidence.get("sess_restart", "ev_restart")
            stored_audit_events = uow.audit_events.list("sess_restart")
            stored_reviews = uow.reviews.list_for_session("sess_restart")

        assert stored_session is not None
        assert stored_session.conversation_id == session.conversation_id
        assert stored_session.review_result == {"score": 92, "status": "completed"}
        assert stored_evidence is not None
        assert stored_evidence.evidence_id == evidence.evidence_id
        assert stored_audit_events == [audit]
        assert stored_reviews == [review]
    finally:
        restarted_engine.dispose()


def test_postgres_replayed_audit_source_waits_for_uncommitted_canonical_event(
    migrated_postgres_engine: Engine,
) -> None:
    session = _session("sess_audit_wait", owner_user_id="owner_audit_wait")
    uow_factory = UnitOfWorkFactory(create_session_factory(migrated_postgres_engine))
    with uow_factory() as uow:
        uow.sessions.create(session)
        uow.commit()

    session_factory = create_session_factory(migrated_postgres_engine)
    main_session = session_factory()
    try:
        main_session.execute(
            text(
                "select 1 from learning_sessions "
                "where session_id = :session_id for update"
            ),
            {"session_id": "sess_audit_wait"},
        )
        canonical = SqlAuditEventRepository(main_session).append(
            "sess_audit_wait",
            "verification.completed",
            "tool",
            {"verified": True},
            source_openhands_event_id="native_audit_wait",
            event_id="evt_audit_wait_canonical",
        )
        worker_application_name = "ai4c_audit_wait_worker"
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _replay_audit_source_with_application_name,
                session_factory,
                worker_application_name,
            )
            _wait_for_worker_lock(migrated_postgres_engine, worker_application_name)
            main_session.commit()
            replay = future.result(timeout=5)
    finally:
        main_session.close()

    assert replay == canonical
    with uow_factory() as uow:
        audit_events = uow.audit_events.list("sess_audit_wait")
    assert [event.event_id for event in audit_events] == ["evt_audit_wait_canonical"]
    assert [event.sequence for event in audit_events] == [1]


def test_postgres_distinct_late_review_is_history_only_after_reviewed_projection(
    postgres_uow_factory: UnitOfWorkFactory,
) -> None:
    session = _session("sess_review_terminal", owner_user_id="owner_review_terminal")
    first_review = _review(
        "rev_terminal_first",
        session,
        review_status="completed",
        source_openhands_event_id="native_review_terminal_first",
    )
    later_review = _review(
        "rev_terminal_later",
        session,
        review_status="completed",
        source_openhands_event_id="native_review_terminal_later",
    ).model_copy(update={"score": 11, "result": {"score": 11, "status": "late"}})

    with postgres_uow_factory() as uow:
        uow.sessions.create(session)
        uow.reviews.add_from_native_event(first_review)
        uow.commit()

    with postgres_uow_factory() as uow:
        terminal_session = uow.sessions.get("sess_review_terminal")
    assert terminal_session is not None
    assert terminal_session.status == "reviewed"
    assert terminal_session.review_result == {"score": 92, "status": "completed"}
    terminal_version = terminal_session.version

    with postgres_uow_factory() as uow:
        stored_later_review = uow.reviews.add_from_native_event(later_review)
        uow.commit()

    assert stored_later_review.review_id == "rev_terminal_later"
    with postgres_uow_factory() as uow:
        stored_session = uow.sessions.get("sess_review_terminal")
        reviews = uow.reviews.list_for_session("sess_review_terminal")

    assert stored_session is not None
    assert stored_session.status == "reviewed"
    assert stored_session.review_result == {"score": 92, "status": "completed"}
    assert stored_session.version == terminal_version
    assert [review.review_id for review in reviews] == [
        "rev_terminal_first",
        "rev_terminal_later",
    ]

    with postgres_uow_factory() as uow:
        duplicate = uow.reviews.add_from_native_event(
            later_review.model_copy(update={"review_id": "rev_terminal_duplicate"})
        )
        uow.commit()

    assert duplicate.review_id == "rev_terminal_later"


@pytest.mark.parametrize("attempt", range(5))
def test_postgres_native_event_idempotency_survives_concurrent_replay(
    postgres_uow_factory: UnitOfWorkFactory,
    attempt: int,
) -> None:
    session_id = f"sess_concurrent_{attempt}"
    audit_source = f"native_audit_concurrent_{attempt}"
    review_source = f"native_review_concurrent_{attempt}"
    session = _session(session_id, owner_user_id="owner_concurrent")
    with postgres_uow_factory() as uow:
        uow.sessions.create(session)
        uow.commit()

    barrier = Barrier(4)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(
                _replay_same_native_events,
                postgres_uow_factory,
                barrier,
                i,
                session_id,
                audit_source,
                review_source,
            )
            for i in range(4)
        ]
        results = [future.result(timeout=10) for future in futures]

    audit_ids = {event_id for event_id, _review_id in results}
    review_ids = {review_id for _event_id, review_id in results}
    assert len(audit_ids) == 1
    assert len(review_ids) == 1

    with postgres_uow_factory() as uow:
        audit_events = uow.audit_events.list(session_id)
        reviews = uow.reviews.list_for_session(session_id)

    assert len(audit_events) == 1
    assert audit_events[0].sequence == 1
    assert audit_events[0].source_openhands_event_id == audit_source
    assert len(reviews) == 1
    assert reviews[0].source_openhands_event_id == review_source


def _replay_audit_source_with_application_name(
    session_factory: sessionmaker[Session],
    application_name: str,
) -> StoredAuditEvent:
    with session_factory() as session:
        session.execute(text(f"set application_name to {_quote_literal(application_name)}"))
        event = SqlAuditEventRepository(session).append(
            "sess_audit_wait",
            "verification.completed",
            "tool",
            {"verified": True},
            source_openhands_event_id="native_audit_wait",
        )
        session.commit()
        return event


def _wait_for_worker_lock(
    engine: Engine,
    application_name: str,
    *,
    timeout_seconds: float = 5.0,
) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.execute(
                text(
                    "select count(*) from pg_stat_activity "
                    "where application_name = :application_name "
                    "and wait_event_type = 'Lock'"
                ),
                {"application_name": application_name},
            ).scalar_one()
        if int(waiting) > 0:
            return
        sleep(0.05)
    pytest.fail("postgres_worker_did_not_block_on_canonical_audit_replay")


def _replay_same_native_events(
    uow_factory: UnitOfWorkFactory,
    barrier: Barrier,
    index: int,
    session_id: str,
    audit_source: str,
    review_source: str,
) -> tuple[str, str]:
    barrier.wait(timeout=5)
    with uow_factory() as uow:
        event = uow.audit_events.append(
            session_id,
            "verification.completed",
            "tool",
            {"verified": True},
            source_openhands_event_id=audit_source,
        )
        review = uow.reviews.add_from_native_event(
            _review(
                f"rev_concurrent_{index}",
                _session(session_id, owner_user_id="owner_concurrent"),
                review_status="completed",
                source_openhands_event_id=review_source,
            )
        )
        uow.commit()
    return event.event_id, review.review_id


def _session(session_id: str, *, owner_user_id: str = "owner_a") -> StoredSession:
    now = datetime.now(UTC)
    return StoredSession(
        session_id=session_id,
        owner_user_id=owner_user_id,
        status="running",
        adapter_mode="openhands-local-scripted-test",
        domain="general",
        title="PostgreSQL replay",
        goal="Verify durable PostgreSQL persistence",
        expected_output=None,
        planned_minutes=20,
        conversation_id=str(uuid5(NAMESPACE_URL, f"focusproof:{session_id}")),
        runtime_mode="openhands-local-scripted-test",
        review_result=None,
        goal_conversation_synced_at=None,
        version=1,
        created_at=now,
        updated_at=now,
    )


def _evidence(session_id: str, evidence_id: str) -> StoredEvidence:
    return StoredEvidence(
        evidence_id=evidence_id,
        session_id=session_id,
        evidence_type="text",
        content_hash=f"sha256:{evidence_id}",
        text_content="PostgreSQL fixture evidence",
        source_url=None,
        metadata={"source": "postgres", "nested": True},
        conversation_synced_at=None,
        created_at=datetime.now(UTC),
    )


def _review(
    review_id: str,
    session: StoredSession,
    *,
    review_status: str,
    source_openhands_event_id: str,
) -> StoredReview:
    return StoredReview(
        review_id=review_id,
        session_id=session.session_id,
        conversation_id=session.conversation_id,
        review_status=review_status,
        score=92 if review_status == "completed" else None,
        result=(
            {"score": 92, "status": "completed"}
            if review_status == "completed"
            else None
        ),
        native_event_count=7,
        source_openhands_event_id=source_openhands_event_id,
        created_at=datetime.now(UTC),
    )


def _require_task3_pgpass() -> None:
    if os.environ.get("PGPASSFILE") != TASK3_PGPASSFILE:
        pytest.fail("postgres_pgpassfile_missing")
    if not os.environ.get("PGCONNECT_TIMEOUT"):
        pytest.fail("postgres_connect_timeout_missing")
    metadata = Path(TASK3_PGPASSFILE).stat()
    if metadata.st_mode & 0o777 != 0o600:
        pytest.fail("postgres_pgpassfile_mode_invalid")


def _base_url() -> str:
    return URL.create(
        "postgresql+psycopg",
        username=PGUSER,
        host=PGHOST,
        port=PGPORT,
        database=PGDATABASE,
    ).render_as_string(hide_password=False)


def _schema_url(schema: str) -> str:
    return URL.create(
        "postgresql+psycopg",
        username=PGUSER,
        host=PGHOST,
        port=PGPORT,
        database=PGDATABASE,
        query={"options": f"-csearch_path={schema},public"},
    ).render_as_string(hide_password=False)


def _quote_identifier(identifier: str) -> str:
    if (
        not identifier
        or not identifier[0].islower()
        or any(not (char.islower() or char.isdigit() or char == "_") for char in identifier)
    ):
        raise AssertionError("invalid postgres test identifier")
    return f'"{identifier}"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _table_names(engine: Engine) -> set[str]:
    return {str(name) for name in inspect(engine).get_table_names()}


def _unique_columns(engine: Engine, table_name: str) -> set[tuple[str, ...]]:
    constraints = inspect(engine).get_unique_constraints(table_name)
    result: set[tuple[str, ...]] = set()
    for constraint in constraints:
        columns = cast("list[Any] | tuple[Any, ...] | None", constraint.get("column_names"))
        result.add(tuple(str(column) for column in columns or ()))
    return result


def _index_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return {
            str(name)
            for name in connection.execute(
                text("select indexname from pg_indexes where schemaname = current_schema()")
            ).scalars()
        }
