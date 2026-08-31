from __future__ import annotations

import asyncio
import os
import stat
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import func, select

from focusproof.contracts.media_scan import MediaScanAuditSnapshot
from focusproof.media_application import ResourceSlotController, SlotBoundMalwareScanner
from focusproof.media_core.ports import MalwareScanVerdict, ReadOnlyMediaSource
from focusproof.persistence.models import (
    MediaCleanReceiptModel,
    MediaScanAttemptModel,
    SpeechResourceSlotModel,
    SpeechTranscriptionRequestModel,
)
from focusproof.persistence.repositories import (
    ResourceSlotLease,
    SpeechAdmissionToken,
    SqlResourceSlotRepository,
    StoredSession,
)
from focusproof.persistence.unit_of_work import UnitOfWorkFactory
from focusproof.speech_core.errors import (
    SpeechAmbiguousError,
    SpeechErrorCode,
    SpeechProviderError,
)
from focusproof.speech_core.idempotency import request_fingerprint
from focusproof.speech_core.models import (
    AudioFacts,
    AudioFormat,
    LanguageHint,
    TranscriptionRequest,
    TranscriptionResult,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def uow_factory(tmp_path: Path) -> Iterator[UnitOfWorkFactory]:
    from focusproof.persistence.database import create_database_engine, create_session_factory
    from focusproof.persistence.models import Base

    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'speech.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = UnitOfWorkFactory(create_session_factory(engine))
    factory.configure_speech(
        active_hmac_key_version="v1",
        hmac_keys={"v1": b"test-hmac-key"},
    )
    _seed(factory)
    try:
        yield factory
    finally:
        engine.dispose()


def _seed(factory: UnitOfWorkFactory) -> None:
    now = datetime.now(UTC)
    with factory() as uow:
        uow.sessions.create(
            StoredSession(
                session_id="sess_1",
                owner_user_id="user_1",
                status="running",
                adapter_mode="openhands-local-scripted-test",
                domain="general",
                title="Speech",
                goal="Test speech",
                expected_output=None,
                planned_minutes=20,
                conversation_id=str(uuid5(NAMESPACE_URL, "focusproof:sess_1")),
                runtime_mode="openhands-local-scripted-test",
                review_result=None,
                goal_conversation_synced_at=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        uow.resource_slots.reconcile("scan", configured_count=1, config_generation=1)
        uow.resource_slots.reconcile("asr", configured_count=1, config_generation=1)
        uow.commit()


def _admit(
    factory: UnitOfWorkFactory,
    *,
    request_fingerprint: str | None = None,
) -> SpeechAdmissionToken:
    with factory() as uow:
        token = uow.speech_requests.admit(
            owner_user_id="user_1",
            session_id="sess_1",
            idempotency_key=str(uuid4()),
            request_fingerprint=request_fingerprint,
            lease_owner="worker-1",
        )
        uow.commit()
    return token


def _application_types() -> tuple[type[Any], type[Any], type[Any]]:
    from focusproof.speech_application import (
        SpeechExecutionAdmission,
        TranscriptionService,
        UploadedSpeechFile,
    )

    return SpeechExecutionAdmission, TranscriptionService, UploadedSpeechFile


class FakeUpload:
    declared_media_type = "audio/wav"

    def __init__(
        self, payload: bytes = b"RIFF-private-audio", failure: Exception | None = None
    ) -> None:
        self.payload = payload
        self.failure = failure
        self.destinations: list[Path] = []

    async def write_to(self, destination: Path, *, deadline: float) -> Any:
        assert time.monotonic() < deadline
        self.destinations.append(destination)
        if self.failure is not None:
            raise self.failure
        destination.write_bytes(self.payload)
        uploaded_type = _application_types()[2]
        return uploaded_type(
            byte_size=len(self.payload),
            streaming_sha256=sha256(self.payload).hexdigest(),
        )


class FakeScanner:
    def __init__(
        self,
        status: str = "clean",
        failure: Exception | None = None,
        *,
        deadline_ms: int = 1000,
        delay_seconds: float = 0.0,
    ) -> None:
        self.status = status
        self.failure = failure
        self.deadline_ms = deadline_ms
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.payloads: list[bytes] = []

    @property
    def audit_snapshot(self) -> MediaScanAuditSnapshot:
        return MediaScanAuditSnapshot(
            scanner_backend="fake",
            definitions_version="test",
            definitions_fresh_at=datetime(2026, 8, 29, tzinfo=UTC),
            definitions_age_seconds=0,
            max_bytes=10 * 1024 * 1024,
            deadline_ms=self.deadline_ms,
            max_concurrent_scans=1,
            socket_timeout_ms=500,
        )

    def scan(self, source: ReadOnlyMediaSource) -> MalwareScanVerdict:
        self.calls += 1
        time.sleep(self.delay_seconds)
        self.payloads.append(source.stream.read())
        if self.failure is not None:
            raise self.failure
        return MalwareScanVerdict(status=self.status, engine="fake")  # type: ignore[arg-type]


class FakeInspector:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls = 0

    async def inspect(
        self,
        path: Path,
        *,
        declared_media_type: str | None,
        deadline: float,
    ) -> AudioFacts:
        assert path.is_file()
        assert declared_media_type == "audio/wav"
        assert time.monotonic() < deadline
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return AudioFacts(
            audio_format=AudioFormat.WAV_PCM,
            media_type="audio/wav",
            codec="pcm",
            byte_size=18,
            duration_ms=800,
        )


class FakeProvider:
    def __init__(
        self,
        factory: UnitOfWorkFactory,
        failure: Exception | None = None,
    ) -> None:
        self.factory = factory
        self.failure = failure
        self.calls = 0

    async def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        deadline: float,
    ) -> TranscriptionResult:
        self.calls += 1
        assert time.monotonic() < deadline
        with self.factory() as uow:
            row = uow._require_session().get(
                SpeechTranscriptionRequestModel, str(request.request_id)
            )
            assert row is not None
            assert row.state == "dispatching"
            assert row.provider_attempts == 1
            assert row.provider_dispatched_at is not None
        if self.failure is not None:
            raise self.failure
        return TranscriptionResult(
            request_id=request.request_id,
            transcript="private candidate",
            provider="dashscope",
            model="qwen3-asr-flash",
        )


class SlowProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        deadline: float,
    ) -> TranscriptionResult:
        del request, deadline
        self.calls += 1
        await asyncio.sleep(60)
        raise AssertionError("provider sleep must be cancelled by the business deadline")


async def _connected() -> bool:
    return False


async def _disconnected() -> bool:
    return True


def _service(
    factory: UnitOfWorkFactory,
    temp_dir: Path,
    *,
    scanner: FakeScanner | None = None,
    inspector: FakeInspector | None = None,
    provider: FakeProvider | None = None,
    file_remover: Callable[[Path], Awaitable[None]] | None = None,
    clock: Callable[[], float] = time.monotonic,
):
    service_type = _application_types()[1]
    return service_type(
        uow_factory=factory,
        malware_scanner=scanner or FakeScanner(),
        audio_inspector=inspector or FakeInspector(),
        scan_slots=ResourceSlotController(factory, lease_seconds=5),
        provider=provider or FakeProvider(factory),
        temp_dir=temp_dir,
        file_remover=file_remover,
        clock=clock,
    )


def _admission(
    factory: UnitOfWorkFactory,
    *,
    deadline: float | None = None,
    request_fingerprint: str | None = None,
):
    admission_type = _application_types()[0]
    return admission_type(
        token=_admit(factory, request_fingerprint=request_fingerprint),
        deadline=deadline if deadline is not None else time.monotonic() + 120,
    )


def _speech_row(factory: UnitOfWorkFactory) -> SpeechTranscriptionRequestModel:
    with factory() as uow:
        row = uow._require_session().scalar(select(SpeechTranscriptionRequestModel))
        assert row is not None
        uow._require_session().expunge(row)
        return row


def _assert_resources_cleared(factory: UnitOfWorkFactory, temp_dir: Path) -> None:
    assert list(temp_dir.glob("*")) == []
    with factory() as uow:
        slot = uow._require_session().scalar(
            select(SpeechResourceSlotModel).where(SpeechResourceSlotModel.resource_kind == "asr")
        )
        assert slot is not None
        assert slot.lease_owner_token is None
        assert slot.work_id is None


async def test_success_commits_dispatch_before_one_call_then_cleans_resources(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    provider = FakeProvider(uow_factory)
    temp_dir = tmp_path / "speech"
    service = _service(uow_factory, temp_dir, provider=provider)

    result = await service.execute(
        _admission(uow_factory), FakeUpload(), LanguageHint.EN, _connected
    )

    assert result.transcript == "private candidate"
    assert provider.calls == 1
    row = _speech_row(uow_factory)
    assert row.state == "succeeded"
    assert row.provider_attempts == 1
    assert row.duration_ms == 800
    expected_fingerprint = request_fingerprint(
        payload_sha256=sha256(b"RIFF-private-audio").hexdigest(),
        language_hint=LanguageHint.EN,
        media_type="audio/wav",
    )
    assert row.request_fingerprint == expected_fingerprint
    assert "private candidate" not in repr(row.__dict__)
    _assert_resources_cleared(uow_factory, temp_dir)


async def test_disconnect_before_dispatch_cancels_without_provider_call(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    provider = FakeProvider(uow_factory)
    temp_dir = tmp_path / "speech"
    service = _service(uow_factory, temp_dir, provider=provider)

    with pytest.raises(asyncio.CancelledError):
        await service.execute(
            _admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _disconnected
        )

    assert provider.calls == 0
    assert _speech_row(uow_factory).state == "cancelled"
    _assert_resources_cleared(uow_factory, temp_dir)


@pytest.mark.parametrize(
    ("failure", "state", "code"),
    [
        (
            SpeechAmbiguousError(),
            "ambiguous",
            SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS,
        ),
        (
            SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_NO_SPEECH),
            "failed_terminal",
            SpeechErrorCode.TRANSCRIPTION_NO_SPEECH,
        ),
    ],
)
async def test_provider_failure_is_finalized_once_and_resources_are_cleared(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
    failure: Exception,
    state: str,
    code: SpeechErrorCode,
) -> None:
    provider = FakeProvider(uow_factory, failure)
    temp_dir = tmp_path / "speech"
    service = _service(uow_factory, temp_dir, provider=provider)

    with pytest.raises(SpeechProviderError) as caught:
        await service.execute(_admission(uow_factory), FakeUpload(), LanguageHint.ZH, _connected)

    assert caught.value.code is code
    assert provider.calls == 1
    assert _speech_row(uow_factory).state == state
    _assert_resources_cleared(uow_factory, temp_dir)


@pytest.mark.parametrize("stage", ["scan", "inspect"])
async def test_predispatch_stage_failure_is_terminal_redacted_and_cleaned(
    tmp_path: Path, uow_factory: UnitOfWorkFactory, stage: str
) -> None:
    private = RuntimeError("private audio /tmp/secret.wav")
    scanner = FakeScanner(failure=private) if stage == "scan" else FakeScanner()
    inspector = FakeInspector(failure=private) if stage == "inspect" else FakeInspector()
    provider = FakeProvider(uow_factory)
    temp_dir = tmp_path / "speech"
    service = _service(
        uow_factory,
        temp_dir,
        scanner=scanner,
        inspector=inspector,
        provider=provider,
    )

    with pytest.raises(SpeechProviderError) as caught:
        await service.execute(_admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _connected)

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_FAILED
    assert "private audio" not in str(caught.value)
    assert "/tmp/secret.wav" not in repr(caught.value)
    assert provider.calls == 0
    assert _speech_row(uow_factory).state == "failed_terminal"
    _assert_resources_cleared(uow_factory, temp_dir)


async def test_upload_failure_uses_bounded_upload_outcome_and_cleans(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    provider = FakeProvider(uow_factory)
    temp_dir = tmp_path / "speech"
    service = _service(uow_factory, temp_dir, provider=provider)

    with pytest.raises(SpeechProviderError) as caught:
        await service.execute(
            _admission(uow_factory),
            FakeUpload(failure=RuntimeError("private upload bytes")),
            LanguageHint.AUTO,
            _connected,
        )

    row = _speech_row(uow_factory)
    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_FAILED
    assert row.state == "failed_terminal"
    assert row.outcome_code == "upload_failed"
    assert provider.calls == 0
    _assert_resources_cleared(uow_factory, temp_dir)


@pytest.mark.parametrize(
    ("stage", "outcome"),
    [("scan", "scan_unavailable"), ("inspect", "inspection_failed")],
)
async def test_stage_failure_persists_specific_bounded_outcome(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
    stage: str,
    outcome: str,
) -> None:
    private = RuntimeError("private stage data")
    scanner = FakeScanner(failure=private) if stage == "scan" else FakeScanner()
    inspector = FakeInspector(failure=private) if stage == "inspect" else FakeInspector()
    service = _service(
        uow_factory,
        tmp_path / "speech",
        scanner=scanner,
        inspector=inspector,
    )

    with pytest.raises(SpeechProviderError):
        await service.execute(_admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _connected)

    assert _speech_row(uow_factory).outcome_code == outcome


async def test_business_deadline_after_dispatch_is_ambiguous_without_retry(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    provider = SlowProvider()
    service = _service(
        uow_factory,
        tmp_path / "speech",
        scanner=FakeScanner(deadline_ms=100),
        provider=provider,  # type: ignore[arg-type]
    )

    with pytest.raises(SpeechAmbiguousError) as caught:
        await service.execute(
            _admission(uow_factory, deadline=time.monotonic() + 8.0),
            FakeUpload(),
            LanguageHint.AUTO,
            _connected,
        )

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS
    assert provider.calls == 1
    assert _speech_row(uow_factory).state == "ambiguous"
    _assert_resources_cleared(uow_factory, tmp_path / "speech")


async def test_cleanup_has_five_second_hard_cap_after_success_commit(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    cleanup_started = asyncio.Event()

    async def blocked_remover(path: Path) -> None:
        path.unlink(missing_ok=True)
        cleanup_started.set()
        await asyncio.sleep(60)

    provider = FakeProvider(uow_factory)
    service = _service(
        uow_factory,
        tmp_path / "speech",
        provider=provider,
        file_remover=blocked_remover,
    )
    started = time.monotonic()

    with pytest.raises(SpeechProviderError) as caught:
        await service.execute(_admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _connected)

    elapsed = time.monotonic() - started
    assert cleanup_started.is_set()
    assert 4.5 <= elapsed < 5.75
    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE
    assert provider.calls == 1
    assert _speech_row(uow_factory).state == "succeeded"
    _assert_resources_cleared(uow_factory, tmp_path / "speech")


async def test_task_cancellation_after_dispatch_is_ambiguous_and_cleaned(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    started = asyncio.Event()

    class CancellableProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def transcribe(
            self,
            request: TranscriptionRequest,
            *,
            deadline: float,
        ) -> TranscriptionResult:
            del request, deadline
            self.calls += 1
            started.set()
            await asyncio.sleep(60)
            raise AssertionError("provider must be cancelled")

    provider = CancellableProvider()
    temp_dir = tmp_path / "speech"
    service = _service(
        uow_factory,
        temp_dir,
        provider=provider,  # type: ignore[arg-type]
    )
    task = asyncio.create_task(
        service.execute(_admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _connected)
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    task.cancel()
    with pytest.raises(SpeechAmbiguousError):
        await task

    assert provider.calls == 1
    assert _speech_row(uow_factory).state == "ambiguous"
    _assert_resources_cleared(uow_factory, temp_dir)


async def test_provider_response_then_success_commit_failure_is_ambiguous(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from focusproof.persistence.repositories import SqlSpeechRequestRepository

    original_finalize = SqlSpeechRequestRepository.finalize

    def fail_success_finalize(
        self: SqlSpeechRequestRepository,
        token: SpeechAdmissionToken,
        *,
        state: str,
        outcome_code: str | None = None,
        latency_ms: int | None = None,
        now: datetime | None = None,
        timeout_ms: int | None = None,
    ) -> bool:
        if state == "succeeded":
            raise RuntimeError("private candidate success commit failed")
        return original_finalize(
            self,
            token,
            state=state,
            outcome_code=outcome_code,
            latency_ms=latency_ms,
            now=now,
            timeout_ms=timeout_ms,
        )

    monkeypatch.setattr(SqlSpeechRequestRepository, "finalize", fail_success_finalize)
    provider = FakeProvider(uow_factory)
    temp_dir = tmp_path / "speech"
    service = _service(uow_factory, temp_dir, provider=provider)

    with pytest.raises(SpeechAmbiguousError) as caught:
        await service.execute(_admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _connected)

    assert provider.calls == 1
    assert "private candidate" not in str(caught.value)
    assert _speech_row(uow_factory).state == "ambiguous"
    _assert_resources_cleared(uow_factory, temp_dir)


async def test_exhausted_business_window_never_dispatches(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    provider = FakeProvider(uow_factory)
    temp_dir = tmp_path / "speech"
    service = _service(uow_factory, temp_dir, provider=provider)

    with pytest.raises(SpeechProviderError) as caught:
        await service.execute(
            _admission(uow_factory, deadline=time.monotonic() + 5),
            FakeUpload(),
            LanguageHint.AUTO,
            _connected,
        )

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_TIMEOUT
    assert provider.calls == 0
    row = _speech_row(uow_factory)
    assert row.state == "failed_terminal"
    assert row.provider_attempts == 0
    _assert_resources_cleared(uow_factory, temp_dir)


async def test_database_transition_is_fenced_by_remaining_business_deadline(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from focusproof.persistence.repositories import SqlSpeechRequestRepository

    original_transition = SqlSpeechRequestRepository.transition
    observed_timeouts: list[int | None] = []
    release = threading.Event()

    def blocked_transition(
        self: SqlSpeechRequestRepository,
        token: SpeechAdmissionToken,
        state: str,
        *args: Any,
        **kwargs: Any,
    ) -> SpeechAdmissionToken:
        observed_timeouts.append(kwargs.get("timeout_ms"))
        release.wait(timeout=0.5)
        return original_transition(self, token, state, *args, **kwargs)

    monkeypatch.setattr(SqlSpeechRequestRepository, "transition", blocked_transition)
    service = _service(uow_factory, tmp_path / "speech")
    timer = threading.Timer(0.5, release.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(SpeechProviderError) as caught:
            await service.execute(
                _admission(
                    uow_factory,
                    deadline=time.monotonic() + 5.1,
                ),
                FakeUpload(),
                LanguageHint.AUTO,
                _connected,
            )
    finally:
        release.set()
        timer.cancel()

    assert time.monotonic() - started < 0.35
    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_TIMEOUT
    assert observed_timeouts
    assert all(timeout is not None and 1 <= timeout <= 150 for timeout in observed_timeouts)
    await asyncio.sleep(0.05)
    assert _speech_row(uow_factory).state == "failed_terminal"
    _assert_resources_cleared(uow_factory, tmp_path / "speech")


async def test_success_finalize_is_fenced_by_remaining_total_deadline(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from focusproof.persistence.repositories import SqlSpeechRequestRepository

    now = [time.monotonic()]
    entry_deadline = now[0] + 120
    release = threading.Event()
    observed_timeouts: list[int | None] = []
    finalize_started_at: list[float] = []
    release_timers: list[threading.Timer] = []
    original_finalize = SqlSpeechRequestRepository.finalize

    def blocked_success_finalize(
        self: SqlSpeechRequestRepository,
        token: SpeechAdmissionToken,
        *,
        state: str,
        outcome_code: str | None = None,
        latency_ms: int | None = None,
        now: datetime | None = None,
        timeout_ms: int | None = None,
    ) -> bool:
        if state == "succeeded":
            observed_timeouts.append(timeout_ms)
            finalize_started_at.append(time.monotonic())
            release_timer = threading.Timer(0.5, release.set)
            release_timers.append(release_timer)
            release_timer.start()
            release.wait(timeout=0.5)
        return original_finalize(
            self,
            token,
            state=state,
            outcome_code=outcome_code,
            latency_ms=latency_ms,
            now=now,
            timeout_ms=timeout_ms,
        )

    class AdvancingProvider(FakeProvider):
        async def transcribe(
            self,
            request: TranscriptionRequest,
            *,
            deadline: float,
        ) -> TranscriptionResult:
            result = await super().transcribe(request, deadline=deadline)
            now[0] = entry_deadline - 5.1
            return result

    monkeypatch.setattr(
        SqlSpeechRequestRepository,
        "finalize",
        blocked_success_finalize,
    )
    provider = AdvancingProvider(uow_factory)
    temp_dir = tmp_path / "speech"
    service = _service(
        uow_factory,
        temp_dir,
        provider=provider,
        clock=lambda: now[0],
    )
    try:
        with pytest.raises(SpeechProviderError) as caught:
            await service.execute(
                _admission(uow_factory, deadline=entry_deadline),
                FakeUpload(),
                LanguageHint.AUTO,
                _connected,
            )
    finally:
        release.set()
        for release_timer in release_timers:
            release_timer.cancel()

    assert finalize_started_at
    assert time.monotonic() - finalize_started_at[0] < 0.3
    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE
    assert observed_timeouts
    assert all(timeout is not None and 1 <= timeout <= 150 for timeout in observed_timeouts)
    for _ in range(50):
        if _speech_row(uow_factory).state == "ambiguous":
            break
        await asyncio.sleep(0.01)
    assert _speech_row(uow_factory).state == "ambiguous"
    assert provider.calls == 1
    _assert_resources_cleared(uow_factory, temp_dir)


async def test_cancellation_during_cleanup_waits_for_privacy_cleanup(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()

    async def gated_remover(path: Path) -> None:
        cleanup_started.set()
        await allow_cleanup.wait()
        path.unlink(missing_ok=True)

    temp_dir = tmp_path / "speech"
    service = _service(
        uow_factory,
        temp_dir,
        file_remover=gated_remover,
    )
    task = asyncio.create_task(
        service.execute(_admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _connected)
    )
    await asyncio.wait_for(cleanup_started.wait(), timeout=2)

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    allow_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _speech_row(uow_factory).state == "succeeded"
    _assert_resources_cleared(uow_factory, temp_dir)


async def test_cleanup_failure_preserves_ambiguous_provider_outcome(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    async def remove_then_fail(path: Path) -> None:
        path.unlink(missing_ok=True)
        raise RuntimeError("private cleanup path")

    provider = FakeProvider(uow_factory, SpeechAmbiguousError())
    temp_dir = tmp_path / "speech"
    service = _service(
        uow_factory,
        temp_dir,
        provider=provider,
        file_remover=remove_then_fail,
    )

    with pytest.raises(SpeechAmbiguousError) as caught:
        await service.execute(_admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _connected)

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS
    assert "private cleanup path" not in str(caught.value)
    assert provider.calls == 1
    assert _speech_row(uow_factory).state == "ambiguous"


async def test_service_enforces_shared_scan_slot_before_scanner(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    from focusproof.media_application import ResourceSlotController

    with uow_factory() as uow:
        uow.resource_slots.reconcile("scan", configured_count=1, config_generation=1)
        occupied = uow.resource_slots.claim(
            "scan",
            work_kind="image",
            work_id="existing-image",
            lease_seconds=60,
        )
        assert occupied is not None
        uow.commit()

    scanner = FakeScanner()
    provider = FakeProvider(uow_factory)
    service_type = _application_types()[1]
    service = service_type(
        uow_factory=uow_factory,
        malware_scanner=scanner,
        scan_slots=ResourceSlotController(uow_factory, lease_seconds=5),
        audio_inspector=FakeInspector(),
        provider=provider,
        temp_dir=tmp_path / "speech",
    )
    started = time.monotonic()
    try:
        with pytest.raises(SpeechProviderError):
            await service.execute(
                _admission(uow_factory, deadline=time.monotonic() + 10.5),
                FakeUpload(),
                LanguageHint.AUTO,
                _connected,
            )
    finally:
        with uow_factory() as uow:
            assert uow.resource_slots.release(occupied)
            uow.commit()

    assert 0.8 <= time.monotonic() - started < 2
    assert scanner.calls == 0
    assert provider.calls == 0
    assert _speech_row(uow_factory).state == "failed_terminal"


async def test_scan_does_not_start_without_full_scanner_contract_budget(
    tmp_path: Path, uow_factory: UnitOfWorkFactory
) -> None:
    scanner = FakeScanner()
    provider = FakeProvider(uow_factory)
    service = _service(
        uow_factory,
        tmp_path / "speech",
        scanner=scanner,
        provider=provider,
    )

    with pytest.raises(SpeechProviderError) as caught:
        await service.execute(
            _admission(uow_factory, deadline=time.monotonic() + 6.5),
            FakeUpload(),
            LanguageHint.AUTO,
            _connected,
        )

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_TIMEOUT
    assert scanner.calls == 0
    assert provider.calls == 0
    assert _speech_row(uow_factory).state == "failed_terminal"


async def test_dispatch_commit_crossing_business_deadline_is_ambiguous_without_call(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [time.monotonic()]
    deadline = now[0] + 120
    provider = FakeProvider(uow_factory)
    service = _service(
        uow_factory,
        tmp_path / "speech",
        provider=provider,
        clock=lambda: now[0],
    )
    original_mark_dispatching = service._mark_dispatching

    async def mark_and_cross_deadline(
        token: SpeechAdmissionToken,
        *,
        deadline: float,
        cleanup_deadline: float,
    ) -> SpeechAdmissionToken:
        updated = await original_mark_dispatching(
            token,
            deadline=deadline,
            cleanup_deadline=cleanup_deadline,
        )
        now[0] = cleanup_deadline - 5
        return updated

    monkeypatch.setattr(service, "_mark_dispatching", mark_and_cross_deadline)

    with pytest.raises(SpeechAmbiguousError):
        await service.execute(
            _admission(uow_factory, deadline=deadline),
            FakeUpload(),
            LanguageHint.AUTO,
            _connected,
        )

    row = _speech_row(uow_factory)
    assert provider.calls == 0
    assert row.provider_attempts == 1
    assert row.provider_dispatched_at is not None
    assert row.state == "ambiguous"


@pytest.mark.parametrize(
    ("status", "expected_result"),
    [
        ("clean", "clean"),
        ("malicious", "malicious"),
        ("oversize", "oversize"),
        ("unavailable", "unavailable"),
        ("timeout", "timeout"),
        ("error", "error"),
        ("unknown", "error"),
    ],
)
async def test_scan_records_metadata_only_audit_without_clean_receipt(
    status: str,
    expected_result: str,
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
) -> None:
    scanner = FakeScanner(status=status)
    provider = FakeProvider(uow_factory)
    temp_dir = tmp_path / "speech"
    service = _service(
        uow_factory,
        temp_dir,
        scanner=scanner,
        provider=provider,
    )

    if status == "clean":
        result = await service.execute(
            _admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _connected
        )
        assert result.provider == "dashscope"
    else:
        with pytest.raises(SpeechProviderError):
            await service.execute(
                _admission(uow_factory),
                FakeUpload(),
                LanguageHint.AUTO,
                _connected,
            )

    with uow_factory() as uow:
        session = uow._require_session()
        attempt = session.scalar(select(MediaScanAttemptModel))
        assert attempt is not None
        session.expunge(attempt)
        attempt_count = session.scalar(select(func.count()).select_from(MediaScanAttemptModel))
        receipt_count = session.scalar(select(func.count()).select_from(MediaCleanReceiptModel))

    assert attempt_count == 1
    assert receipt_count == 0
    assert attempt.artifact_sha256 == sha256(b"RIFF-private-audio").hexdigest()
    assert attempt.content_type == "audio/wav"
    assert attempt.scan_result == expected_result
    assert attempt.scanner_backend == "fake"
    assert attempt.idempotency_key.startswith("speech-scan:")
    assert provider.calls == (1 if status == "clean" else 0)


async def test_shared_scan_slot_passes_bounded_repository_timeouts(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim_timeouts: list[int | None] = []
    release_timeouts: list[int | None] = []
    original_claim = SqlResourceSlotRepository.claim
    original_release = SqlResourceSlotRepository.release

    def capture_claim(
        self: SqlResourceSlotRepository,
        resource_kind: str,
        *args: Any,
        **kwargs: Any,
    ):
        timeout_ms = kwargs.pop("timeout_ms", None)
        if resource_kind == "scan":
            claim_timeouts.append(timeout_ms)
        return original_claim(self, resource_kind, *args, **kwargs)

    def capture_release(
        self: SqlResourceSlotRepository,
        lease: Any,
        *args: Any,
        **kwargs: Any,
    ):
        timeout_ms = kwargs.pop("timeout_ms", None)
        if lease.resource_kind == "scan":
            release_timeouts.append(timeout_ms)
        return original_release(self, lease, *args, **kwargs)

    monkeypatch.setattr(SqlResourceSlotRepository, "claim", capture_claim)
    monkeypatch.setattr(SqlResourceSlotRepository, "release", capture_release)
    scanner = FakeScanner(deadline_ms=1000)
    service = _service(
        uow_factory,
        tmp_path / "speech",
        scanner=scanner,
    )

    await service.execute(_admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _connected)

    assert claim_timeouts
    assert release_timeouts
    assert all(timeout is not None and 1 <= timeout <= 1000 for timeout in claim_timeouts)
    assert all(timeout is not None and 1 <= timeout <= 2000 for timeout in release_timeouts)


async def test_slot_bound_scanner_reserves_fresh_release_deadline() -> None:
    class SlowController:
        def __init__(self) -> None:
            self.release_deadline: float | None = None

        def claim(
            self,
            *,
            work_kind: str,
            work_id: str,
            deadline: float,
        ) -> ResourceSlotLease:
            del work_kind, work_id, deadline
            time.sleep(0.8)
            return ResourceSlotLease("scan", 0, "lease-token", 1)

        def release(
            self,
            lease: ResourceSlotLease,
            *,
            deadline: float | None = None,
        ) -> bool:
            del lease
            self.release_deadline = deadline
            return deadline is not None and deadline >= time.monotonic() + 1.5

    controller = SlowController()
    scanner = FakeScanner(deadline_ms=1000, delay_seconds=0.3)
    bounded = SlotBoundMalwareScanner(
        scanner,
        controller,  # type: ignore[arg-type]
        work_kind="speech",
    )
    payload = b"private-audio"
    source = ReadOnlyMediaSource(
        stream=BytesIO(payload),
        byte_size=len(payload),
        streaming_sha256=sha256(payload).hexdigest(),
    )

    verdict = await asyncio.to_thread(bounded.scan, source)

    assert verdict.status == "clean"
    assert controller.release_deadline is not None
    assert controller.release_deadline >= time.monotonic() + 1.4


async def test_asr_slot_passes_business_and_cleanup_repository_timeouts(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim_timeouts: list[int | None] = []
    release_timeouts: list[int | None] = []
    original_claim = SqlResourceSlotRepository.claim
    original_release = SqlResourceSlotRepository.release

    def capture_claim(
        self: SqlResourceSlotRepository,
        resource_kind: str,
        *args: Any,
        **kwargs: Any,
    ):
        if resource_kind == "asr":
            claim_timeouts.append(kwargs.get("timeout_ms"))
        return original_claim(self, resource_kind, *args, **kwargs)

    def capture_release(
        self: SqlResourceSlotRepository,
        lease: ResourceSlotLease,
        *args: Any,
        **kwargs: Any,
    ):
        if lease.resource_kind == "asr":
            release_timeouts.append(kwargs.get("timeout_ms"))
        return original_release(self, lease, *args, **kwargs)

    monkeypatch.setattr(SqlResourceSlotRepository, "claim", capture_claim)
    monkeypatch.setattr(SqlResourceSlotRepository, "release", capture_release)
    service = _service(uow_factory, tmp_path / "speech")

    await service.execute(_admission(uow_factory), FakeUpload(), LanguageHint.AUTO, _connected)

    assert claim_timeouts
    assert release_timeouts
    assert all(timeout is not None and 1 <= timeout <= 115_000 for timeout in claim_timeouts)
    assert all(timeout is not None and 1 <= timeout <= 120_000 for timeout in release_timeouts)


async def test_service_forces_private_temp_directory_mode_independent_of_umask(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
) -> None:
    temp_dir = tmp_path / "speech"
    service = _service(uow_factory, temp_dir)
    previous_umask = os.umask(0)
    try:
        await service.execute(
            _admission(uow_factory),
            FakeUpload(),
            LanguageHint.AUTO,
            _connected,
        )
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(temp_dir.stat().st_mode) == 0o700


async def test_scan_cancellation_waits_for_worker_fence_before_cleanup_returns(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingScanner(FakeScanner):
        def scan(self, source: ReadOnlyMediaSource) -> MalwareScanVerdict:
            started.set()
            release.wait(timeout=1)
            return super().scan(source)

    temp_dir = tmp_path / "speech"
    service = _service(
        uow_factory,
        temp_dir,
        scanner=BlockingScanner(deadline_ms=1000),
    )
    task = asyncio.create_task(
        service.execute(
            _admission(uow_factory),
            FakeUpload(),
            LanguageHint.AUTO,
            _connected,
        )
    )
    assert await asyncio.to_thread(started.wait, 1)

    task.cancel()
    await asyncio.sleep(0.05)
    try:
        assert not task.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    with uow_factory() as uow:
        session = uow._require_session()
        scan_slot = session.scalar(
            select(SpeechResourceSlotModel).where(SpeechResourceSlotModel.resource_kind == "scan")
        )
        attempt_count = session.scalar(select(func.count()).select_from(MediaScanAttemptModel))
        assert scan_slot is not None
        assert scan_slot.lease_owner_token is None
        assert attempt_count == 0
    assert _speech_row(uow_factory).state == "cancelled"


async def test_symlink_temp_directory_fails_closed_without_writing_target(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_bytes(b"keep")
    temp_dir = tmp_path / "speech"
    temp_dir.symlink_to(outside, target_is_directory=True)
    service = _service(uow_factory, temp_dir)

    with pytest.raises(SpeechProviderError) as caught:
        await service.execute(
            _admission(uow_factory),
            FakeUpload(),
            LanguageHint.AUTO,
            _connected,
        )

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_FAILED
    assert marker.read_bytes() == b"keep"
    assert list(outside.iterdir()) == [marker]
    assert _speech_row(uow_factory).state == "failed_terminal"


async def test_preexisting_audio_symlink_is_rejected_and_unlinked_without_target_write(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
) -> None:
    from starlette.datastructures import Headers, UploadFile

    from focusproof.api.speech_routes import StreamingSpeechUpload

    temp_dir = tmp_path / "speech"
    temp_dir.mkdir(mode=0o700)
    target = tmp_path / "target.audio"
    target.write_bytes(b"do-not-overwrite")
    admission = _admission(uow_factory)
    request_path = temp_dir / f"{admission.token.request_id}.audio"
    request_path.symlink_to(target)
    upload_file = UploadFile(
        file=BytesIO(b"RIFF-private-audio"),
        filename="voice.wav",
        headers=Headers({"content-type": "audio/wav"}),
    )
    service = _service(uow_factory, temp_dir)

    with pytest.raises(SpeechProviderError) as caught:
        await service.execute(
            admission,
            StreamingSpeechUpload(upload_file),
            LanguageHint.AUTO,
            _connected,
        )

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_FAILED
    assert target.read_bytes() == b"do-not-overwrite"
    assert not request_path.exists()
    row = _speech_row(uow_factory)
    assert row.state == "failed_terminal"
    assert row.outcome_code == "upload_failed"
    _assert_resources_cleared(uow_factory, temp_dir)
