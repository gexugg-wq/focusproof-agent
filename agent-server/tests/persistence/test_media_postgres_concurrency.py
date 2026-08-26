from __future__ import annotations

import os
from argparse import Namespace
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import JSON, create_engine, func, inspect, insert, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from focusproof.media_core.models import (
    FinalizeMediaOutcome,
    FinalizeMediaRequest,
    MediaLease,
    MediaReservationRequest,
)
from focusproof.persistence.models import (
    EvidenceModel,
    MediaArtifactModel,
    MediaIngestionReservationModel,
)
from focusproof.persistence.repositories import MediaQuotaExceededError
from focusproof.persistence.unit_of_work import UnitOfWorkFactory

from .test_session_repository import _session

pytestmark = pytest.mark.postgres_media
MiB = 1024 * 1024

_MEDIA_RESERVATION_INSERT_COLUMNS = (
    "reservation_id",
    "media_item_id",
    "owner_id",
    "session_id",
    "idempotency_key",
    "fingerprint",
    "slot",
    "status",
    "active",
    "expires_at",
    "canonical_artifact_id",
    "evidence_id",
    "intent_action",
    "completion_mode",
    "staged_object_key",
    "staged_manifest_id",
    "media_type",
    "normalized_sha256",
    "normalized_byte_size",
    "learner_explanation",
    "attributes_json",
    "result_json",
    "rejection_reason",
    "created_at",
    "updated_at",
)


def _invalid_media_reservation_rows() -> tuple[
    tuple[str, ...], tuple[tuple[str, dict[str, object]], ...]
]:
    durable_facts = _durable_media_reservation_facts()
    return _MEDIA_RESERVATION_INSERT_COLUMNS, (
        (
            "active-json-null",
            _media_reservation_values("active-json-null", attributes_json=JSON.NULL),
        ),
        (
            "pending-json-null",
            _media_reservation_values(
                "pending",
                **(durable_facts | {"status": "PENDING_REFERENCE", "result_json": JSON.NULL}),
            ),
        ),
        (
            "completed-json-null",
            _media_reservation_values(
                "completed",
                **(
                    durable_facts
                    | {
                        "status": "COMPLETED",
                        "active": None,
                        "completion_mode": "ADOPTED",
                        "attributes_json": JSON.NULL,
                    }
                ),
            ),
        ),
        (
            "rejected-json-null",
            _media_reservation_values(
                "rejected", status="REJECTED", active=None, result_json=JSON.NULL
            ),
        ),
        (
            "expired-json-null",
            _media_reservation_values(
                "expired", status="EXPIRED", active=None, attributes_json=JSON.NULL
            ),
        ),
    )


def _durable_media_reservation_facts() -> dict[str, object]:
    return {
        "canonical_artifact_id": "media_pg_canonical",
        "evidence_id": "ev_pg_matrix",
        "intent_action": "MARK_REFERENCED",
        "staged_object_key": "opaque-pg-staged",
        "staged_manifest_id": "manifest-pg-staged",
        "media_type": "application/test",
        "normalized_sha256": "d" * 64,
        "normalized_byte_size": 7,
        "learner_explanation": "explanation",
        "attributes_json": {},
        "result_json": {},
    }


def _media_reservation_values(name: str, **overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "reservation_id": f"res_pg_{name}",
        "media_item_id": f"media_pg_{name}",
        "owner_id": "owner",
        "session_id": "sess_pg_matrix",
        "idempotency_key": f"key-{name}",
        "fingerprint": f"fp-{name}",
        "slot": 0,
        "status": "ACTIVE",
        "active": True,
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
    values.update(overrides)
    assert tuple(values) == _MEDIA_RESERVATION_INSERT_COLUMNS
    return values


@pytest.fixture
def postgres_media_url() -> str:
    raw = os.environ.get("FOCUSPROOF_TEST_POSTGRES_MEDIA_URL")
    if not raw:
        pytest.skip("FOCUSPROOF_TEST_POSTGRES_MEDIA_URL is not set")
    try:
        parsed = make_url(raw)
    except ArgumentError:
        pytest.fail("FOCUSPROOF_TEST_POSTGRES_MEDIA_URL is not a valid database URL")
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("FOCUSPROOF_TEST_POSTGRES_MEDIA_URL must be PostgreSQL")
    if parsed.database is None or not parsed.database.startswith("focusproof_test_"):
        pytest.fail(
            "FOCUSPROOF_TEST_POSTGRES_MEDIA_URL must name a disposable focusproof_test_ database"
        )
    return raw


@pytest.fixture
def postgres_media_factory(
    postgres_media_url: str,
    project_root: Path,
) -> Iterator[UnitOfWorkFactory]:
    engine = create_engine(postgres_media_url, pool_size=6)
    config = _migration_config(project_root, make_url(postgres_media_url))
    try:
        command.upgrade(config, "head")
        expected_head = ScriptDirectory.from_config(config).get_current_head()
        with engine.connect() as connection:
            actual_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert actual_head == expected_head
        factory = UnitOfWorkFactory(sessionmaker(engine, class_=Session, expire_on_commit=False))
        yield factory
    finally:
        try:
            command.downgrade(config, "base")
        finally:
            engine.dispose()


def _migration_config(project_root: Path, database_url: URL) -> Config:
    config = Config(project_root / "alembic.ini")
    config.set_main_option("script_location", str(project_root / "agent-server" / "migrations"))
    rendered = database_url.render_as_string(hide_password=False)
    config.cmd_opts = Namespace(x=[f"database_url={rendered}"])
    return config


def _reserve(factory: UnitOfWorkFactory, session_id: str, key: str) -> MediaLease:
    with factory() as uow:
        lease = uow.media.reserve(MediaReservationRequest("owner", session_id, key, f"fp-{key}"))
        uow.commit()
        return lease


def _request(lease: MediaLease, digest: str, size: int) -> FinalizeMediaRequest:
    return FinalizeMediaRequest(
        lease,
        lease.media_item_id,
        f"private-{lease.media_item_id}",
        f"manifest-{lease.media_item_id}",
        "application/test",
        digest,
        size,
        "explanation",
        {},
    )


def test_three_visible_then_four_concurrent_reserves_allow_one(
    postgres_media_factory: UnitOfWorkFactory,
) -> None:
    with postgres_media_factory() as uow:
        uow.sessions.create(_session().model_copy(update={"owner_user_id": "owner"}))
        uow.commit()
    for index in range(3):
        lease = _reserve(postgres_media_factory, "sess_1", f"seed-{index}")
        with postgres_media_factory() as uow:
            outcome = uow.media.finalize(_request(lease, str(index) * 64, 1))
            uow.commit()
        with postgres_media_factory() as uow:
            uow.media.confirm_reference(outcome.reference_intent)
            uow.commit()

    barrier = Barrier(4)

    def attempt(index: int) -> bool:
        try:
            with postgres_media_factory() as uow:
                uow._require_session().connection()
                barrier.wait()
                uow.media.reserve(
                    MediaReservationRequest("owner", "sess_1", f"race-{index}", f"fp-race-{index}")
                )
                uow.commit()
            return True
        except MediaQuotaExceededError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 1


def test_concurrent_pending_distinct_bytes_cannot_oversell_session(
    postgres_media_factory: UnitOfWorkFactory,
) -> None:
    with postgres_media_factory() as uow:
        uow.sessions.create(_session().model_copy(update={"owner_user_id": "owner"}))
        uow.commit()
    leases = [_reserve(postgres_media_factory, "sess_1", f"bytes-{index}") for index in range(2)]
    barrier = Barrier(2)

    def attempt(index: int) -> bool:
        try:
            with postgres_media_factory() as uow:
                uow._require_session().connection()
                barrier.wait()
                uow.media.finalize(_request(leases[index], str(index + 7) * 64, 12 * MiB))
                uow.commit()
            return True
        except MediaQuotaExceededError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, range(2))) == 1


def test_cross_session_same_hash_converges_through_pending_reference(
    postgres_media_factory: UnitOfWorkFactory,
) -> None:
    with postgres_media_factory() as uow:
        uow.sessions.create(_session("sess_1").model_copy(update={"owner_user_id": "owner"}))
        uow.sessions.create(_session("sess_2").model_copy(update={"owner_user_id": "owner"}))
        uow.commit()
    leases = [
        _reserve(postgres_media_factory, f"sess_{index + 1}", f"same-{index}") for index in range(2)
    ]
    barrier = Barrier(2)

    def finalize(index: int) -> FinalizeMediaOutcome:
        with postgres_media_factory() as uow:
            uow._require_session().connection()
            barrier.wait()
            outcome = uow.media.finalize(_request(leases[index], "a" * 64, 8 * MiB))
            uow.commit()
            return outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(finalize, range(2)))
    winner = next(item for item in outcomes if item.reference_intent.action == "MARK_REFERENCED")
    follower = next(item for item in outcomes if item.reference_intent.action == "ABORT_STAGED")
    assert winner.evidence_visible is False
    assert follower.evidence_visible is False

    with postgres_media_factory() as uow:
        uow.media.confirm_reference(winner.reference_intent)
        uow.commit()
    follower_index = (
        0 if follower.reference_intent.staged.reservation_id == leases[0].reservation_id else 1
    )
    with postgres_media_factory() as uow:
        replay = uow.media.find_idempotent_outcome(
            "owner",
            f"sess_{follower_index + 1}",
            f"same-{follower_index}",
            f"fp-same-{follower_index}",
        )
        uow.commit()
    assert replay is not None
    assert replay.reference_intent.action == "NOOP"
    assert replay.evidence_visible is True
    assert replay.result == follower.result

    with postgres_media_factory() as uow:
        session = uow._require_session()
        assert session.scalar(select(func.count(MediaArtifactModel.media_item_id))) == 1
        assert session.scalar(select(func.count(EvidenceModel.evidence_id))) == 2
        assert (
            session.scalar(
                select(func.count(MediaIngestionReservationModel.reservation_id)).where(
                    MediaIngestionReservationModel.status == "ACTIVE"
                )
            )
            == 0
        )


def test_concurrent_confirm_replay_and_reject_converge(
    postgres_media_factory: UnitOfWorkFactory,
) -> None:
    with postgres_media_factory() as uow:
        uow.sessions.create(_session().model_copy(update={"owner_user_id": "owner"}))
        uow.commit()
    lease = _reserve(postgres_media_factory, "sess_1", "converge")
    with postgres_media_factory() as uow:
        pending = uow.media.finalize(_request(lease, "c" * 64, 8 * MiB))
        uow.commit()
    barrier = Barrier(3)

    def confirm() -> str:
        with postgres_media_factory() as uow:
            uow._require_session().connection()
            barrier.wait()
            uow.media.confirm_reference(pending.reference_intent)
            uow.commit()
        return "confirmed"

    def replay() -> str:
        with postgres_media_factory() as uow:
            uow._require_session().connection()
            barrier.wait()
            outcome = uow.media.find_idempotent_outcome(
                "owner", "sess_1", "converge", "fp-converge"
            )
            uow.commit()
        assert outcome is not None
        return outcome.reference_intent.action

    def reject() -> str:
        with postgres_media_factory() as uow:
            uow._require_session().connection()
            barrier.wait()
            uow.media.reject(lease, "concurrent terminal noop")
            uow.commit()
        return "rejected"

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(confirm), pool.submit(replay), pool.submit(reject)]
        results = [future.result(timeout=10) for future in futures]
    assert "confirmed" in results

    with postgres_media_factory() as uow:
        session = uow._require_session()
        reservation = session.get(MediaIngestionReservationModel, lease.reservation_id)
        assert reservation is not None and reservation.status == "COMPLETED"
        assert session.scalar(select(func.count(EvidenceModel.evidence_id))) == 1
        final = uow.media.find_idempotent_outcome("owner", "sess_1", "converge", "fp-converge")
        uow.commit()
    assert final is not None
    assert final.reference_intent.action == "NOOP"
    assert final.evidence_visible is True


def test_postgres_alembic_0005_upgrade_downgrade_reupgrade(
    postgres_media_url: str,
    project_root: Path,
) -> None:
    base_url = make_url(postgres_media_url)
    schema = f"task2b_{uuid4().hex}"
    migration_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    admin_engine = create_engine(postgres_media_url)
    migration_engine = create_engine(migration_url)
    config = _migration_config(project_root, migration_url)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        command.upgrade(config, "0005_media_artifacts")
        with migration_engine.connect() as connection:
            version_column_length = connection.scalar(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'alembic_version' AND column_name = 'version_num'"
                )
            )
        assert version_column_length == 32
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        expected_head = ScriptDirectory.from_config(config).get_current_head()
        with migration_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == expected_head
            )
        inspector = inspect(migration_engine)
        assert {"media_ingestion_reservations", "media_artifacts"} <= set(
            inspector.get_table_names()
        )
        indexes = {
            item["name"]: item for item in inspector.get_indexes("media_ingestion_reservations")
        }
        active_slot = indexes["uq_media_ingestion_active_owner_slot"]
        assert active_slot["unique"] is True
        assert "active IS TRUE" in str(active_slot["dialect_options"]["postgresql_where"])
        assert "ck_media_ingestion_state_payload_matrix" in {
            item["name"] for item in inspector.get_check_constraints("media_ingestion_reservations")
        }
        reservation_fk_names = {
            item["name"] for item in inspector.get_foreign_keys("media_ingestion_reservations")
        }
        artifact_fk_names = {item["name"] for item in inspector.get_foreign_keys("media_artifacts")}
        assert "fk_media_ingestion_canonical_artifact" in reservation_fk_names
        assert "fk_media_artifacts_reservation" in artifact_fk_names

        now = "CURRENT_TIMESTAMP"
        with migration_engine.begin() as connection:
            connection.execute(
                text(
                    f"""
                    INSERT INTO learning_sessions (
                        session_id, owner_user_id, status, adapter_mode, domain,
                        title, goal, conversation_id, runtime_mode, version,
                        created_at, updated_at
                    ) VALUES (
                        'sess_pg_matrix', 'owner', 'running', 'test', 'general',
                        'Matrix', 'Validate PostgreSQL matrix',
                        '44444444-4444-4444-4444-444444444444', 'test', 1,
                        {now}, {now}
                    )
                    """
                )
            )
            connection.execute(
                text(
                    f"""
                    INSERT INTO media_artifacts (
                        media_item_id, owner_id, creator_reservation_id,
                        opaque_object_key, manifest_id, media_type,
                        normalized_sha256, normalized_byte_size, state, created_at
                    ) VALUES (
                        'media_pg_canonical', 'owner', NULL,
                        'opaque-pg-canonical', 'manifest-pg-canonical', 'application/test',
                        '{"d" * 64}', 7, 'PENDING_REFERENCE', {now}
                    )
                    """
                )
            )
        columns, invalid_rows = _invalid_media_reservation_rows()
        assert len(columns) == 25
        assert columns == tuple(MediaIngestionReservationModel.__table__.columns.keys())
        for case, values in invalid_rows:
            assert len(values) == len(columns), case
            assert tuple(values) == columns, case
            with pytest.raises(IntegrityError) as caught, migration_engine.begin() as connection:
                connection.execute(insert(MediaIngestionReservationModel), values)
            original = caught.value.orig
            constraint_name = getattr(getattr(original, "diag", None), "constraint_name", None)
            assert constraint_name == "ck_media_ingestion_state_payload_matrix", case

        active_values = _media_reservation_values("active-python-none", slot=1)
        with migration_engine.begin() as connection:
            connection.execute(insert(MediaIngestionReservationModel), active_values)
            stored_nulls = connection.execute(
                select(
                    MediaIngestionReservationModel.attributes_json.is_(None),
                    MediaIngestionReservationModel.result_json.is_(None),
                ).where(
                    MediaIngestionReservationModel.reservation_id == active_values["reservation_id"]
                )
            ).one()
        assert stored_nulls == (True, True)

        for index, (completion_mode, intent_action) in enumerate(
            (
                ("ADOPTED", "MARK_REFERENCED"),
                ("FOLLOWER", "ABORT_STAGED"),
                ("DIRECT_REUSE", "ABORT_STAGED"),
            ),
            start=2,
        ):
            values = _media_reservation_values(
                f"valid-{completion_mode.lower()}",
                **(
                    _durable_media_reservation_facts()
                    | {
                        "slot": index,
                        "status": "COMPLETED",
                        "active": None,
                        "completion_mode": completion_mode,
                        "intent_action": intent_action,
                    }
                ),
            )
            with migration_engine.begin() as connection:
                connection.execute(insert(MediaIngestionReservationModel), values)
                stored_payloads = connection.execute(
                    select(
                        MediaIngestionReservationModel.attributes_json,
                        MediaIngestionReservationModel.result_json,
                    ).where(
                        MediaIngestionReservationModel.reservation_id == values["reservation_id"]
                    )
                ).one()
            assert stored_payloads == ({}, {})

        command.downgrade(config, "0005_media_artifacts")
        inspector = inspect(migration_engine)
        assert {"media_ingestion_reservations", "media_artifacts"} <= set(
            inspector.get_table_names()
        )
        assert {"scan_attempts", "clean_receipts", "pending_clean_receipts"}.isdisjoint(
            inspector.get_table_names()
        )
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0005_media_artifacts"
            )

        command.upgrade(config, "head")
        inspector = inspect(migration_engine)
        assert {"media_ingestion_reservations", "media_artifacts"} <= set(
            inspector.get_table_names()
        )
        with migration_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == expected_head
            )
    finally:
        migration_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
