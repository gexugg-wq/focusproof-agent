from __future__ import annotations

import os
from argparse import Namespace
from collections.abc import Iterator
from multiprocessing import get_context
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from focusproof.media_application import ResourceSlotController
from focusproof.persistence.database import create_database_engine, create_session_factory
from focusproof.persistence.models import Base, SpeechResourceSlotModel
from focusproof.persistence.repositories import ResourceSlotLease
from focusproof.persistence.unit_of_work import UnitOfWorkFactory


@pytest.fixture
def sqlite_uow_factory(tmp_path: Path) -> Iterator[UnitOfWorkFactory]:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'task3.sqlite3'}"
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    try:
        yield UnitOfWorkFactory(create_session_factory(engine))
    finally:
        engine.dispose()


def test_scan_pool_shrink_drains_occupied_surplus_and_fences_stale_release(
    sqlite_uow_factory: UnitOfWorkFactory,
) -> None:
    controller = ResourceSlotController(sqlite_uow_factory, lease_seconds=60)
    controller.reconcile(configured_count=2, config_generation=1)
    first = controller.claim(work_kind="image", work_id="image-one", deadline=monotonic() + 1)
    second = controller.claim(work_kind="speech", work_id="speech-one", deadline=monotonic() + 1)
    assert isinstance(first, ResourceSlotLease)
    assert isinstance(second, ResourceSlotLease)

    controller.reconcile(configured_count=1, config_generation=2)
    stale = ResourceSlotLease(
        first.resource_kind,
        first.slot_number,
        first.lease_owner_token,
        first.lease_generation - 1,
    )
    assert not controller.release(stale)
    assert controller.release(first)
    assert controller.release(second)

    replacement = controller.claim(
        work_kind="speech", work_id="speech-two", deadline=monotonic() + 1
    )
    assert isinstance(replacement, ResourceSlotLease)
    assert (
        controller.claim(work_kind="image", work_id="image-two", deadline=monotonic() + 0.1) is None
    )
    assert controller.release(replacement)


def test_scan_slot_wait_uses_the_caller_deadline(
    sqlite_uow_factory: UnitOfWorkFactory,
) -> None:
    controller = ResourceSlotController(sqlite_uow_factory, lease_seconds=60)
    controller.reconcile(configured_count=1, config_generation=1)
    first = controller.claim(
        work_kind="image", work_id="held-image", deadline=monotonic() + 1
    )
    assert isinstance(first, ResourceSlotLease)

    def release_after_delay() -> None:
        sleep(0.05)
        assert controller.release(first)

    releaser = Thread(target=release_after_delay)
    releaser.start()
    started = monotonic()
    replacement = controller.claim(
        work_kind="speech",
        work_id="waiting-speech",
        deadline=monotonic() + 1,
    )
    releaser.join(timeout=1)

    assert isinstance(replacement, ResourceSlotLease)
    assert monotonic() - started >= 0.04
    assert controller.release(replacement)


@pytest.fixture
def postgres_url() -> str:
    raw = os.environ.get("FOCUSPROOF_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("FOCUSPROOF_TEST_POSTGRES_URL is not set")
    parsed = make_url(raw)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("FOCUSPROOF_TEST_POSTGRES_URL must be PostgreSQL")
    if parsed.database is None or not parsed.database.startswith("focusproof_test_task3_"):
        pytest.fail("database must be disposable and start with focusproof_test_task3_")
    return raw


@pytest.fixture
def postgres_factories(
    postgres_url: str,
) -> Iterator[tuple[UnitOfWorkFactory, UnitOfWorkFactory]]:
    project_root = Path(__file__).resolve().parents[3]
    engine_a = create_engine(postgres_url, pool_size=8, max_overflow=8)
    engine_b = create_engine(postgres_url, pool_size=8, max_overflow=8)
    config = Config(project_root / "alembic.ini")
    config.set_main_option("script_location", str(project_root / "agent-server" / "migrations"))
    config.cmd_opts = Namespace(
        x=[f"database_url={make_url(postgres_url).render_as_string(hide_password=False)}"]
    )
    command.upgrade(config, "head")
    try:
        yield (
            UnitOfWorkFactory(sessionmaker(engine_a, class_=Session, expire_on_commit=False)),
            UnitOfWorkFactory(sessionmaker(engine_b, class_=Session, expire_on_commit=False)),
        )
    finally:
        command.downgrade(config, "base")
        engine_a.dispose()
        engine_b.dispose()


def _process_claim_speech_slot(
    postgres_url: str,
    start: Any,
    hold: Any,
    results: Any,
    worker: int,
) -> None:
    engine = create_engine(postgres_url, pool_size=1)
    factory = UnitOfWorkFactory(sessionmaker(engine, class_=Session, expire_on_commit=False))
    controller = ResourceSlotController(factory, lease_seconds=30)
    start.wait(10)
    lease = controller.claim(
        work_kind="speech",
        work_id=f"speech-process-{worker}",
        deadline=monotonic() + 0.75,
    )
    results.put("acquired" if lease is not None else "saturated")
    if lease is not None:
        hold.wait(10)
        controller.release(lease)
    engine.dispose()


@pytest.mark.postgres
def test_independent_engines_and_processes_share_image_and_speech_scan_slots(
    postgres_factories: tuple[UnitOfWorkFactory, UnitOfWorkFactory],
    postgres_url: str,
) -> None:
    factory_a, factory_b = postgres_factories
    image_controller = ResourceSlotController(factory_a, lease_seconds=30)
    observer_controller = ResourceSlotController(factory_b, lease_seconds=30)
    image_controller.reconcile(configured_count=2, config_generation=1)
    image_lease = image_controller.claim(
        work_kind="image", work_id="image-parent", deadline=monotonic() + 1
    )
    assert isinstance(image_lease, ResourceSlotLease)

    context = get_context("fork")
    start = context.Event()
    hold = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_process_claim_speech_slot,
            args=(postgres_url, start, hold, results, worker),
        )
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=10) for _ in processes]
    assert outcomes.count("acquired") == 1
    assert outcomes.count("saturated") == 3
    assert (
        observer_controller.claim(
            work_kind="image", work_id="image-observer", deadline=monotonic() + 0.1
        )
        is None
    )

    with factory_b() as uow:
        occupied_work_kinds = tuple(
            uow._require_session().scalars(
                select(SpeechResourceSlotModel.work_kind).where(
                    SpeechResourceSlotModel.resource_kind == "scan",
                    SpeechResourceSlotModel.lease_owner_token.is_not(None),
                )
            )
        )
    assert len(occupied_work_kinds) == 2
    assert set(occupied_work_kinds) == {"image", "speech"}

    hold.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert image_controller.release(image_lease)

    image_controller.reconcile(configured_count=3, config_generation=2)
    leases = [
        image_controller.claim(
            work_kind="image" if index % 2 == 0 else "speech",
            work_id=f"drain-{index}",
            deadline=monotonic() + 1,
        )
        for index in range(3)
    ]
    assert all(isinstance(lease, ResourceSlotLease) for lease in leases)
    image_controller.reconcile(configured_count=1, config_generation=3)
    for lease in leases:
        assert lease is not None
        assert image_controller.release(lease)
    survivor = image_controller.claim(
        work_kind="speech", work_id="survivor", deadline=monotonic() + 1
    )
    assert isinstance(survivor, ResourceSlotLease)
    assert (
        observer_controller.claim(work_kind="image", work_id="retired", deadline=monotonic() + 0.1)
        is None
    )
    assert image_controller.release(survivor)
