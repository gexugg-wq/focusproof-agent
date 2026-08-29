from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from focusproof.api.speech_admission import (
    SpeechAdmissionGate,
    SpeechRecoverySweeper,
    SpeechTaskRegistry,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class _Repo:
    recovered: int
    calls: int = 0
    now: datetime | None = None

    def recover_expired(self, *, now: datetime | None = None) -> int:
        self.calls += 1
        self.now = now
        return self.recovered


class _Slots:
    def __init__(self) -> None:
        self.claimed: list[str] = []
        self.released = 0

    def claim(
        self,
        resource_kind: str,
        *,
        work_kind: str,
        work_id: str,
        lease_seconds: int,
        now: datetime | None = None,
        timeout_ms: int | None = None,
    ) -> object:
        del work_id, lease_seconds, now, timeout_ms
        assert work_kind == "speech"
        self.claimed.append(resource_kind)
        return object()

    def release(self, lease: object, *, timeout_ms: int | None = None) -> bool:
        del lease, timeout_ms
        self.released += 1
        return True


class _Uow:
    def __init__(self, repo: _Repo, slots: _Slots | None = None) -> None:
        self.speech_requests = repo
        self.resource_slots = slots or _Slots()
        self.committed = False

    def __enter__(self) -> _Uow:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def commit(self) -> None:
        self.committed = True


def _age(path: Path, seconds: int) -> None:
    timestamp = datetime.now(UTC).timestamp() - seconds
    os.utime(path, (timestamp, timestamp))


async def test_startup_recovery_classifies_expired_leases_and_only_deletes_uuid_temp(
    tmp_path: Path,
) -> None:
    stale_uuid = tmp_path / f"{uuid4()}.audio"
    stale_uuid.write_bytes(b"private")
    _age(stale_uuid, 500)
    malformed = tmp_path / "client-name.audio"
    malformed.write_bytes(b"preserve")
    _age(malformed, 500)
    wrong_suffix = tmp_path / f"{uuid4()}.wav"
    wrong_suffix.write_bytes(b"preserve")
    _age(wrong_suffix, 500)
    fresh_uuid = tmp_path / f"{uuid4()}.audio"
    fresh_uuid.write_bytes(b"preserve")
    repo = _Repo(recovered=2)
    slots = _Slots()
    sweeper = SpeechRecoverySweeper(
        uow_factory=lambda: _Uow(repo, slots),
        temp_dir=tmp_path,
        stale_after_seconds=120,
        interval_seconds=30,
    )
    now = datetime.now(UTC)

    counters = await sweeper.recover_once(now=now)

    assert counters.expired_leases == 2
    assert counters.stale_temp_files == 2
    assert counters.resource_slot_sweeps == 2
    assert slots.claimed == ["scan", "asr"]
    assert slots.released == 2
    assert repo.calls == 1
    assert repo.now == now
    assert not stale_uuid.exists()
    assert malformed.read_bytes() == b"preserve"
    assert not wrong_suffix.exists()
    assert fresh_uuid.read_bytes() == b"preserve"


async def test_periodic_sweeper_is_lifespan_owned_and_closes_cleanly(
    tmp_path: Path,
) -> None:
    repo = _Repo(recovered=0)
    sweeper = SpeechRecoverySweeper(
        uow_factory=lambda: _Uow(repo),
        temp_dir=tmp_path,
        stale_after_seconds=120,
        interval_seconds=0.01,
    )

    await sweeper.start()
    await asyncio.sleep(0.03)
    await sweeper.close()
    calls_after_close = repo.calls
    await asyncio.sleep(0.02)

    assert calls_after_close >= 1
    assert repo.calls == calls_after_close
    assert sweeper.running is False


async def test_shutdown_closes_admission_bounded_drains_cancels_and_fences() -> None:
    gate = SpeechAdmissionGate(opened=True)
    registry = SpeechTaskRegistry()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    fenced = 0

    async def blocked() -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def fence() -> None:
        nonlocal fenced
        fenced += 1

    task = registry.create_task(blocked())
    await asyncio.wait_for(started.wait(), timeout=1)
    started_at = asyncio.get_running_loop().time()

    await registry.close(
        gate=gate,
        grace_seconds=0.01,
        fence=fence,
    )

    elapsed = asyncio.get_running_loop().time() - started_at
    assert gate.is_open is False
    assert cancelled.is_set()
    assert task.cancelled()
    assert registry.active_count == 0
    assert fenced == 1
    assert elapsed < 0.5


async def test_shutdown_deadline_does_not_wait_for_noncooperative_task_and_fences() -> None:
    gate = SpeechAdmissionGate(opened=True)
    registry = SpeechTaskRegistry()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    fenced = asyncio.Event()
    fence_finished = asyncio.Event()

    async def noncooperative() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await asyncio.Event().wait()

    async def fence() -> None:
        fenced.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.Event().wait()
        finally:
            fence_finished.set()

    task = registry.create_task(noncooperative())
    await asyncio.wait_for(started.wait(), timeout=1)
    started_at = asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(
            registry.close(
                gate=gate,
                grace_seconds=0.04,
                fence=fence,
            ),
            timeout=0.2,
        )

        assert asyncio.get_running_loop().time() - started_at < 0.15
        assert gate.is_open is False
        assert cancelled.is_set()
        assert fenced.is_set()
        assert fence_finished.is_set()
        assert task.cancelled()
        assert registry.active_count == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
