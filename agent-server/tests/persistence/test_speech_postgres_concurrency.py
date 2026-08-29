from __future__ import annotations

import os
import hashlib
from argparse import Namespace
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier
from uuid import uuid4
from time import monotonic

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from focusproof.persistence.repositories import (
    SpeechQuotaExceededError,
    SqlSpeechRequestRepository,
)
from focusproof.persistence.unit_of_work import UnitOfWorkFactory

from .test_session_repository import _session

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_url() -> str:
    raw = os.environ.get("FOCUSPROOF_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("FOCUSPROOF_TEST_POSTGRES_URL is not set")
    parsed = make_url(raw)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("FOCUSPROOF_TEST_POSTGRES_URL must be PostgreSQL")
    if parsed.database is None or not parsed.database.startswith("focusproof_test_"):
        pytest.fail("database must be disposable and start with focusproof_test_")
    return raw


@pytest.fixture
def postgres_factory(
    postgres_url: str,
    project_root: Path,
) -> Iterator[UnitOfWorkFactory]:
    engine = create_engine(postgres_url, pool_size=16, max_overflow=16)
    config = Config(project_root / "alembic.ini")
    config.set_main_option(
        "script_location", str(project_root / "agent-server" / "migrations")
    )
    config.cmd_opts = Namespace(
        x=[f"database_url={make_url(postgres_url).render_as_string(hide_password=False)}"]
    )
    command.upgrade(config, "head")
    command.downgrade(config, "0007_drop_monad_evidence_claims")
    command.upgrade(config, "head")
    factory = UnitOfWorkFactory(
        sessionmaker(engine, class_=Session, expire_on_commit=False)
    )
    factory.configure_speech(
        active_hmac_key_version="v1", hmac_keys={"v1": b"secret"}
    )
    try:
        yield factory
    finally:
        command.downgrade(config, "base")
        engine.dispose()


def test_independent_workers_cannot_overshoot_session_or_owner_quota(
    postgres_factory: UnitOfWorkFactory,
) -> None:
    with postgres_factory() as uow:
        uow.sessions.create(_session("sess_pg_a"))
        uow.sessions.create(_session("sess_pg_b"))
        uow.commit()
    barrier = Barrier(40)

    def admit(index: int) -> bool:
        barrier.wait()
        session_id = "sess_pg_a" if index < 25 else "sess_pg_b"
        try:
            with postgres_factory() as uow:
                uow.speech_requests.admit(
                    owner_user_id="dev-anonymous-user",
                    session_id=session_id,
                    idempotency_key=str(uuid4()),
                    request_fingerprint="f" * 64,
                    lease_owner=f"worker-{index}",
                )
                uow.commit()
            return True
        except SpeechQuotaExceededError:
            return False

    with ThreadPoolExecutor(max_workers=40) as pool:
        accepted = list(pool.map(admit, range(40)))
    assert sum(accepted[:25]) <= 20
    assert sum(accepted[25:]) <= 20
    assert sum(accepted) == 30


def test_four_asr_slots_cannot_become_five(
    postgres_factory: UnitOfWorkFactory,
) -> None:
    with postgres_factory() as uow:
        uow.resource_slots.reconcile("asr", configured_count=4, config_generation=1)
        uow.commit()
    barrier = Barrier(12)

    def claim(index: int) -> bool:
        barrier.wait()
        with postgres_factory() as uow:
            lease = uow.resource_slots.claim(
                "asr",
                work_kind="speech",
                work_id=f"work-{index}",
                lease_seconds=60,
            )
            uow.commit()
            return lease is not None


def _process_admit(
    postgres_url: str,
    session_id: str,
    start_event: object,
    results: object,
    worker_number: int,
    idempotency_key: str | None = None,
) -> None:
    engine = create_engine(postgres_url, pool_size=1)
    factory = UnitOfWorkFactory(
        sessionmaker(engine, class_=Session, expire_on_commit=False)
    )
    factory.configure_speech(
        active_hmac_key_version="v1",
        hmac_keys={"v1": b"secret"},
    )
    start_event.wait(10)
    try:
        with factory() as uow:
            uow.speech_requests.admit(
                owner_user_id="dev-anonymous-user",
                session_id=session_id,
                idempotency_key=idempotency_key or str(uuid4()),
                request_fingerprint="f" * 64,
                lease_owner=f"process-{worker_number}",
            )
            uow.commit()
        results.put("accepted")
    except SpeechQuotaExceededError:
        results.put("quota")
    except Exception as exc:
        results.put(type(exc).__name__)
    finally:
        engine.dispose()


def test_two_processes_and_engines_cannot_cross_session_boundary(
    postgres_factory: UnitOfWorkFactory,
    postgres_url: str,
) -> None:
    session_id = "sess_pg_process"
    with postgres_factory() as uow:
        uow.sessions.create(_session(session_id))
        for index in range(19):
            uow.speech_requests.admit(
                owner_user_id="dev-anonymous-user",
                session_id=session_id,
                idempotency_key=str(uuid4()),
                request_fingerprint="f" * 64,
                lease_owner=f"seed-{index}",
            )
        uow.commit()

    context = get_context("fork")
    start_event = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_process_admit,
            args=(postgres_url, session_id, start_event, results, index),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    start_event.set()
    outcomes = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sorted(outcomes) == ["accepted", "quota"]


def test_postgres_admission_locks_owner_before_session(
    postgres_factory: UnitOfWorkFactory,
    postgres_url: str,
) -> None:
    session_id = "sess_pg_lock_order"
    with postgres_factory() as uow:
        uow.sessions.create(_session(session_id))
        uow.commit()

    engine = create_engine(postgres_url, pool_size=1)
    observed: list[int] = []

    def capture_locks(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, context, executemany
        if "pg_advisory_xact_lock" in statement and isinstance(parameters, dict):
            observed.append(int(parameters["lock_key"]))

    event.listen(engine, "before_cursor_execute", capture_locks)
    factory = UnitOfWorkFactory(
        sessionmaker(engine, class_=Session, expire_on_commit=False)
    )
    factory.configure_speech(
        active_hmac_key_version="v1",
        hmac_keys={"v1": b"secret"},
    )
    with factory() as uow:
        uow.speech_requests.admit(
            owner_user_id="dev-anonymous-user",
            session_id=session_id,
            idempotency_key=str(uuid4()),
            request_fingerprint="f" * 64,
            lease_owner="lock-order",
        )
        uow.commit()
    engine.dispose()

    def expected(namespace: str, value: str) -> int:
        digest = hashlib.sha256(f"{namespace}:{value}".encode()).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    assert observed[:2] == [
        expected("speech-owner", "dev-anonymous-user"),
        expected("speech-session", session_id),
    ]


def test_postgres_advisory_lock_wait_is_bounded(
    postgres_factory: UnitOfWorkFactory,
    postgres_url: str,
) -> None:
    session_id = "sess_pg_lock_timeout"
    owner_id = "dev-anonymous-user"
    with postgres_factory() as uow:
        uow.sessions.create(_session(session_id))
        uow.commit()

    digest = hashlib.sha256(f"speech-owner:{owner_id}".encode()).digest()
    owner_lock = int.from_bytes(digest[:8], byteorder="big", signed=True)
    holder_engine = create_engine(postgres_url, pool_size=1)
    contender_engine = create_engine(postgres_url, pool_size=1)
    with holder_engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": owner_lock},
        )
        contender = Session(contender_engine, expire_on_commit=False)
        repository = SqlSpeechRequestRepository(
            contender,
            active_hmac_key_version="v1",
            hmac_keys={"v1": b"secret"},
            lock_timeout_ms=100,
        )
        started = monotonic()
        with pytest.raises(OperationalError):
            repository.admit(
                owner_user_id=owner_id,
                session_id=session_id,
                idempotency_key=str(uuid4()),
                request_fingerprint="f" * 64,
                lease_owner="timeout-contender",
            )
        assert monotonic() - started < 2
        contender.rollback()
        contender.close()
        transaction.rollback()
    holder_engine.dispose()
    contender_engine.dispose()


def test_two_processes_cannot_duplicate_one_hmac_key(
    postgres_factory: UnitOfWorkFactory,
    postgres_url: str,
) -> None:
    session_id = "sess_pg_duplicate"
    with postgres_factory() as uow:
        uow.sessions.create(_session(session_id))
        uow.commit()
    key = str(uuid4())
    context = get_context("fork")
    start_event = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_process_admit,
            args=(postgres_url, session_id, start_event, results, index, key),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    start_event.set()
    outcomes = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sorted(outcomes) == ["SpeechAdmissionError", "accepted"]


def test_postgres_recovery_fences_stale_generation(
    postgres_factory: UnitOfWorkFactory,
) -> None:
    session_id = "sess_pg_recovery"
    past = datetime.now(UTC) - timedelta(minutes=5)
    with postgres_factory() as uow:
        uow.sessions.create(_session(session_id))
        token = uow.speech_requests.admit(
            owner_user_id="dev-anonymous-user",
            session_id=session_id,
            idempotency_key=str(uuid4()),
            request_fingerprint="f" * 64,
            lease_owner="stale-worker",
            now=past,
            lease_seconds=1,
        )
        uow.commit()
    with postgres_factory() as uow:
        assert uow.speech_requests.recover_expired(now=datetime.now(UTC)) == 1
        assert not uow.speech_requests.finalize(
            token,
            state="failed_terminal",
            outcome_code="invalid_audio",
        )
        uow.commit()
