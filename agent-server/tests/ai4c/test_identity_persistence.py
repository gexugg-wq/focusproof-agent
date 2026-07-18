from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from threading import Barrier
from typing import cast

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import Engine, Table, create_engine, func, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.schema import CreateTable

from focusproof.persistence.database import create_database_engine, create_session_factory
from focusproof.persistence.models import VerifiedPrincipalModel
from focusproof.persistence.providers import (
    IdentityStorageIsolationError,
    IdentityStoragePaths,
    InvalidPrincipalIdentityError,
    PrincipalDisabledError,
    UowPrincipalResolver,
    select_identity_storage_paths,
)
from focusproof.persistence.repositories import SqlPrincipalRepository
from focusproof.persistence.unit_of_work import UnitOfWorkFactory


def _config(database_url: str) -> Config:
    root = Path(__file__).resolve().parents[3]
    config = Config(root / "alembic.ini")
    config.set_main_option("script_location", str(root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _factory(tmp_path: Path, name: str = "identity.sqlite3") -> tuple[Engine, UnitOfWorkFactory]:
    url = f"sqlite+pysqlite:///{tmp_path / name}"
    command.upgrade(_config(url), "head")
    engine = create_database_engine(url)
    return engine, UnitOfWorkFactory(create_session_factory(engine))


def _count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.scalar(select(func.count()).select_from(VerifiedPrincipalModel)) or 0)


@pytest.mark.parametrize(
    ("issuer", "subject"),
    [
        ("", "subject-a"),
        ("   ", "subject-a"),
        (" https://issuer.example", "subject-a"),
        ("https://issuer.example ", "subject-a"),
        ("https://issuer.example", ""),
        ("https://issuer.example", "\t"),
        ("https://issuer.example", " subject-a"),
        ("https://issuer.example", "subject-a\n"),
    ],
)
def test_edge_whitespace_is_rejected_without_persisting(
    tmp_path: Path, issuer: str, subject: str
) -> None:
    engine, factory = _factory(tmp_path)
    try:
        with pytest.raises(InvalidPrincipalIdentityError) as exc_info:
            UowPrincipalResolver(factory).resolve(issuer=issuer, subject=subject)
        assert exc_info.value.code == "invalid_principal_identity"
        assert _count(engine) == 0
    finally:
        engine.dispose()


def test_issuer_and_subject_are_stored_and_matched_exactly(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path)
    try:
        resolver = UowPrincipalResolver(factory)
        ids = {
            resolver.resolve(issuer="https://Issuer.example", subject="Subject-A"),
            resolver.resolve(issuer="https://issuer.example", subject="Subject-A"),
            resolver.resolve(issuer="https://issuer.example/", subject="Subject-A"),
            resolver.resolve(issuer="https://issuer.example", subject="subject-a"),
        }
        assert len(ids) == 4
        expected = {
            ("https://Issuer.example", "Subject-A"),
            ("https://issuer.example", "Subject-A"),
            ("https://issuer.example/", "Subject-A"),
            ("https://issuer.example", "subject-a"),
        }
        with factory() as uow:
            rows = [
                uow.principals.get_exact(issuer=issuer, subject=subject)
                for issuer, subject in expected
            ]
        assert all(row is not None for row in rows)
        assert {(row.issuer, row.subject) for row in rows if row is not None} == expected
    finally:
        engine.dispose()


def test_exact_identity_is_stable_across_restart(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'restart.sqlite3'}"
    command.upgrade(_config(url), "head")
    first_engine = create_database_engine(url)
    first_resolver = UowPrincipalResolver(UnitOfWorkFactory(create_session_factory(first_engine)))
    first = first_resolver.resolve(issuer="https://issuer.example/exact", subject="subject/restart")
    assert first_resolver.resolve(issuer="https://issuer.example/exact", subject="subject/restart") == first
    first_engine.dispose()

    restarted_engine = create_database_engine(url)
    try:
        restarted = UowPrincipalResolver(UnitOfWorkFactory(create_session_factory(restarted_engine)))
        assert restarted.resolve(issuer="https://issuer.example/exact", subject="subject/restart") == first
        assert _count(restarted_engine) == 1
    finally:
        restarted_engine.dispose()


def test_principal_id_is_random_opaque_and_not_identity_derived(tmp_path: Path) -> None:
    issuer = "https://opaque-issuer.example/IdentityCase"
    subject = "opaque-subject-DO-NOT-EMBED"
    generated: list[str] = []
    for index in range(2):
        root = tmp_path / str(index)
        root.mkdir()
        engine, factory = _factory(root)
        try:
            generated.append(UowPrincipalResolver(factory).resolve(issuer=issuer, subject=subject))
        finally:
            engine.dispose()
    assert generated[0] != generated[1]
    for principal_id in generated:
        assert principal_id.startswith("principal_")
        assert len(principal_id) == len("principal_") + 32
        assert issuer not in principal_id
        assert subject not in principal_id


def test_concurrent_first_resolution_returns_only_database_winner(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path)
    resolver = UowPrincipalResolver(factory)
    workers = 8
    barrier = Barrier(workers)

    def resolve_once(_: int) -> str:
        barrier.wait(timeout=5)
        return resolver.resolve(issuer="https://concurrent.example", subject="subject-concurrent")

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(resolve_once, range(workers)))
        assert len(set(results)) == 1
        assert _count(engine) == 1
        with factory() as uow:
            winner = uow.principals.get_exact(
                issuer="https://concurrent.example", subject="subject-concurrent"
            )
        assert winner is not None
        assert results == [winner.principal_id] * workers
    finally:
        engine.dispose()


def test_disabled_principal_is_not_replaced(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path)
    resolver = UowPrincipalResolver(factory)
    try:
        principal_id = resolver.resolve(issuer="https://disabled.example", subject="subject-disabled")
        with factory() as uow:
            assert uow.principals.set_active(principal_id, active=False)
            uow.commit()
        with pytest.raises(PrincipalDisabledError) as exc_info:
            resolver.resolve(issuer="https://disabled.example", subject="subject-disabled")
        assert exc_info.value.code == "principal_disabled"
        assert _count(engine) == 1
    finally:
        engine.dispose()


def test_state_change_timestamp_moves_only_on_real_state_transition(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path)
    try:
        principal_id = UowPrincipalResolver(factory).resolve(
            issuer="https://state.example", subject="subject-state"
        )
        with factory() as uow:
            before = uow.principals.get_exact(
                issuer="https://state.example", subject="subject-state"
            )
            assert before is not None
            assert not uow.principals.set_active(principal_id, active=True)
            uow.commit()
        with factory() as uow:
            unchanged = uow.principals.get_exact(
                issuer="https://state.example", subject="subject-state"
            )
            assert unchanged is not None
            assert unchanged.state_changed_at == before.state_changed_at
            assert uow.principals.set_active(principal_id, active=False)
            uow.commit()
        with factory() as uow:
            disabled = uow.principals.get_exact(
                issuer="https://state.example", subject="subject-state"
            )
        assert disabled is not None
        assert disabled.state_changed_at >= unchanged.state_changed_at
    finally:
        engine.dispose()


def test_migration_does_not_claim_or_rewrite_anonymous_history(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'anonymous.sqlite3'}"
    config = _config(url)
    command.upgrade(config, "0001_initial_focusproof_schema")
    engine = create_database_engine(url)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO learning_sessions (
                session_id, owner_user_id, status, adapter_mode, domain, title, goal,
                conversation_id, runtime_mode, version, created_at, updated_at
            ) VALUES (
                'sess_anonymous', 'dev-anonymous-user', 'running',
                'openhands-local-scripted-test', 'general', 'History', 'Retain history',
                '33333333-3333-3333-3333-333333333333',
                'openhands-local-scripted-test', 1, :now, :now
            )
        """), {"now": now})
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    factory = UnitOfWorkFactory(create_session_factory(engine))
    try:
        UowPrincipalResolver(factory).resolve(issuer="https://new.example", subject="new-subject")
        with factory() as uow:
            historical = uow.sessions.get("sess_anonymous")
        assert historical is not None
        assert historical.owner_user_id == "dev-anonymous-user"
        assert _count(engine) == 1
    finally:
        engine.dispose()


def test_storage_decision_isolates_anonymous_local_dev(tmp_path: Path) -> None:
    local = IdentityStoragePaths(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'local.sqlite3'}",
        conversation_root=tmp_path / "local-conversations",
    )
    verified = IdentityStoragePaths(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'verified.sqlite3'}",
        conversation_root=tmp_path / "verified-conversations",
    )
    assert select_identity_storage_paths("local-dev", anonymous_local_dev=local, verified=verified) == local
    assert select_identity_storage_paths("staging", anonymous_local_dev=local, verified=verified) == verified
    assert select_identity_storage_paths("production", anonymous_local_dev=local, verified=verified) == verified
    with pytest.raises(IdentityStorageIsolationError):
        select_identity_storage_paths("deterministic-test", anonymous_local_dev=local, verified=verified)


@pytest.mark.parametrize("shared", ["database", "conversation"])
def test_storage_decision_rejects_shared_storage(tmp_path: Path, shared: str) -> None:
    local = IdentityStoragePaths(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'local.sqlite3'}",
        conversation_root=tmp_path / "local-conversations",
    )
    verified = IdentityStoragePaths(
        database_url=local.database_url if shared == "database" else f"sqlite+pysqlite:///{tmp_path / 'verified.sqlite3'}",
        conversation_root=local.conversation_root if shared == "conversation" else tmp_path / "verified-conversations",
    )
    with pytest.raises(IdentityStorageIsolationError):
        select_identity_storage_paths("local-dev", anonymous_local_dev=local, verified=verified)


def test_migration_upgrade_downgrade_reupgrade(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'migration.sqlite3'}"
    config = _config(url)
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    try:
        assert "verified_principals" in inspect(engine).get_table_names()
        uniques = inspect(engine).get_unique_constraints("verified_principals")
        assert any(item["name"] == "uq_verified_principals_issuer_subject" for item in uniques)
        assert inspect(engine).get_foreign_keys("verified_principals") == []
    finally:
        engine.dispose()
    command.downgrade(config, "0001_initial_focusproof_schema")
    engine = create_database_engine(url)
    try:
        assert "learning_sessions" in inspect(engine).get_table_names()
        assert "verified_principals" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    try:
        assert "verified_principals" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_schema_and_migration_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    table = cast(Table, VerifiedPrincipalModel.__table__)
    ddl = str(CreateTable(table).compile(dialect=dialect))
    assert "verified_principals" in ddl
    assert "UNIQUE (issuer, subject)" in ddl
    output = StringIO()
    config = _config("postgresql+psycopg://focusproof:placeholder@localhost/focusproof")
    config.output_buffer = output
    command.upgrade(config, "head", sql=True)
    assert "uq_verified_principals_issuer_subject" in output.getvalue()


def test_database_unavailable_is_not_mapped(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'missing' / 'db.sqlite3'}")
    resolver = UowPrincipalResolver(UnitOfWorkFactory(create_session_factory(engine)))
    try:
        with pytest.raises(OperationalError):
            resolver.resolve(issuer="https://issuer.example", subject="subject-a")
    finally:
        engine.dispose()


def test_non_target_integrity_error_is_not_treated_as_identity_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, factory = _factory(tmp_path)

    def raise_unrelated(repository: SqlPrincipalRepository, record: object) -> object:
        del repository, record
        raise IntegrityError("INSERT", {}, RuntimeError("unrelated_constraint"))

    monkeypatch.setattr(SqlPrincipalRepository, "add", raise_unrelated)
    try:
        with pytest.raises(IntegrityError, match="unrelated_constraint"):
            UowPrincipalResolver(factory).resolve(issuer="https://issuer.example", subject="subject-integrity")
        assert _count(engine) == 0
    finally:
        engine.dispose()
