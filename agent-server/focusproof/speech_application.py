from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid5

from focusproof.contracts.media_scan import (
    ScanRejectionCode,
    ScanResultKind,
    default_scan_rejection_code,
)
from focusproof.media_application import ResourceSlotController, SlotBoundMalwareScanner
from focusproof.media_core.models import MediaScanAttempt
from focusproof.media_core.ports import MalwareScanner, MalwareScanStatus, ReadOnlyMediaSource
from focusproof.persistence.repositories import (
    ResourceSlotLease,
    SpeechAdmissionToken,
    SpeechLeaseStateError,
)
from focusproof.persistence.unit_of_work import UnitOfWorkFactory
from focusproof.speech_core.errors import (
    SpeechAmbiguousError,
    SpeechError,
    SpeechErrorCode,
    SpeechProviderError,
)
from focusproof.speech_core.models import (
    LanguageHint,
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptionState,
)
from focusproof.speech_core.ports import AudioInspector, SpeechTranscriptionProvider

_CLEANUP_RESERVE_SECONDS = 5.0
_SCAN_DEADLINE_MARGIN_SECONDS = 0.25
_SLOT_POLL_SECONDS = 0.01
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class SpeechExecutionAdmission:
    token: SpeechAdmissionToken
    deadline: float

    def __post_init__(self) -> None:
        if not isinstance(self.token, SpeechAdmissionToken):
            raise ValueError("admission token is invalid")
        if not math.isfinite(self.deadline):
            raise ValueError("admission deadline must be finite")


@dataclass(frozen=True, slots=True)
class UploadedSpeechFile:
    byte_size: int
    streaming_sha256: str

    def __post_init__(self) -> None:
        if self.byte_size <= 0:
            raise ValueError("uploaded audio must not be empty")
        if len(self.streaming_sha256) != 64:
            raise ValueError("uploaded audio digest is invalid")


class SpeechUpload(Protocol):
    declared_media_type: str | None

    async def write_to(
        self,
        destination: Path,
        *,
        deadline: float,
    ) -> UploadedSpeechFile: ...


DisconnectProbe = Callable[[], Awaitable[bool]]
FileRemover = Callable[[Path], Awaitable[None]]


class _StageFailure(SpeechProviderError):
    def __init__(self, code: SpeechErrorCode, outcome_code: str) -> None:
        super().__init__(code)
        self.outcome_code = outcome_code


class TranscriptionService:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        malware_scanner: MalwareScanner,
        scan_slots: ResourceSlotController,
        audio_inspector: AudioInspector,
        provider: SpeechTranscriptionProvider,
        temp_dir: Path,
        file_remover: FileRemover | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not temp_dir.is_absolute():
            raise ValueError("speech temp directory must be absolute")
        self._uow_factory = uow_factory
        self._scanner = SlotBoundMalwareScanner(
            malware_scanner,
            scan_slots,
            work_kind="speech",
        )
        self._inspector = audio_inspector
        self._provider = provider
        self._temp_dir = temp_dir
        self._file_remover = file_remover or self._remove_file
        self._clock = clock

    async def execute(
        self,
        admission: SpeechExecutionAdmission,
        upload: SpeechUpload,
        language_hint: LanguageHint,
        disconnect_probe: DisconnectProbe,
    ) -> TranscriptionResult:
        token = admission.token
        request_path = self._temp_dir / f"{token.request_id}.audio"
        business_deadline = admission.deadline - _CLEANUP_RESERVE_SECONDS
        asr_lease: ResourceSlotLease | None = None
        dispatched = False
        success_committed = False
        cleanup_error: SpeechProviderError | None = None
        ambiguous_classified = False
        try:
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            self._ensure_business_time(business_deadline)
            await self._check_disconnect(disconnect_probe)
            token = self._transition(token, TranscriptionState.UPLOADING)
            try:
                uploaded = await self._run_before_deadline(
                    upload.write_to(request_path, deadline=business_deadline),
                    business_deadline,
                )
            except SpeechError:
                raise
            except Exception:
                raise _StageFailure(SpeechErrorCode.TRANSCRIPTION_FAILED, "upload_failed") from None
            self._ensure_business_time(business_deadline)
            await self._check_disconnect(disconnect_probe)
            token = self._transition(
                token,
                TranscriptionState.SCANNING,
                media_type=upload.declared_media_type,
                byte_size=uploaded.byte_size,
            )
            try:
                await self._scan(
                    token.request_id,
                    request_path,
                    uploaded,
                    content_type=upload.declared_media_type or "application/octet-stream",
                    deadline=business_deadline,
                )
            except SpeechError:
                raise
            except Exception:
                raise _StageFailure(
                    SpeechErrorCode.TRANSCRIPTION_FAILED, "scan_unavailable"
                ) from None
            self._ensure_business_time(business_deadline)
            await self._check_disconnect(disconnect_probe)
            try:
                facts = await self._run_before_deadline(
                    self._inspector.inspect(
                        request_path,
                        declared_media_type=upload.declared_media_type,
                        deadline=business_deadline,
                    ),
                    business_deadline,
                )
            except SpeechError as exc:
                raise _StageFailure(exc.code, "inspection_failed") from None
            except Exception:
                raise _StageFailure(
                    SpeechErrorCode.TRANSCRIPTION_FAILED, "inspection_failed"
                ) from None
            token = self._transition(
                token,
                TranscriptionState.INSPECTING,
                media_type=facts.media_type,
                byte_size=facts.byte_size,
                duration_ms=facts.duration_ms,
            )
            self._ensure_business_time(business_deadline)
            await self._check_disconnect(disconnect_probe)
            asr_lease = await self._claim_asr_slot(
                request_id=token.request_id,
                deadline=business_deadline,
                release_deadline=admission.deadline,
            )
            if asr_lease is None:
                raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_TIMEOUT)
            self._ensure_business_time(business_deadline)
            await self._check_disconnect(disconnect_probe)
            token = self._mark_dispatching(token)
            dispatched = True
            if self._clock() >= business_deadline:
                raise SpeechAmbiguousError()
            provider_started = self._clock()
            try:
                result = await self._run_before_deadline(
                    self._provider.transcribe(
                        TranscriptionRequest(
                            request_id=UUID(token.request_id),
                            audio_path=request_path,
                            facts=facts,
                            language_hint=language_hint,
                        ),
                        deadline=business_deadline,
                    ),
                    business_deadline,
                )
            except SpeechProviderError as exc:
                if exc.code is SpeechErrorCode.TRANSCRIPTION_TIMEOUT:
                    raise SpeechAmbiguousError() from None
                raise
            latency_ms = max(0, round((self._clock() - provider_started) * 1000))
            if not self._finalize(
                token,
                state=TranscriptionState.SUCCEEDED,
                latency_ms=latency_ms,
            ):
                raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE)
            success_committed = True
            return result
        except asyncio.CancelledError:
            if dispatched:
                ambiguous_classified = True
                self._finalize(
                    token,
                    state=TranscriptionState.AMBIGUOUS,
                    outcome_code=SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS.value,
                )
                raise SpeechAmbiguousError() from None
            self._finalize(
                token,
                state=TranscriptionState.CANCELLED,
                outcome_code="client_cancelled",
            )
            raise
        except SpeechAmbiguousError:
            ambiguous_classified = True
            self._finalize(
                token,
                state=TranscriptionState.AMBIGUOUS,
                outcome_code=SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS.value,
            )
            raise
        except SpeechError as exc:
            self._finalize(
                token,
                state=TranscriptionState.FAILED_TERMINAL,
                outcome_code=(
                    exc.outcome_code if isinstance(exc, _StageFailure) else exc.code.value
                ),
            )
            raise
        except Exception:
            if dispatched:
                ambiguous_classified = True
                self._finalize(
                    token,
                    state=TranscriptionState.AMBIGUOUS,
                    outcome_code=SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS.value,
                )
                raise SpeechAmbiguousError() from None
            self._finalize(
                token,
                state=TranscriptionState.FAILED_TERMINAL,
                outcome_code=SpeechErrorCode.TRANSCRIPTION_FAILED.value,
            )
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_FAILED) from None
        finally:
            try:
                await self._cleanup(
                    request_path,
                    asr_lease,
                    deadline=admission.deadline,
                )
            except SpeechProviderError as exc:
                cleanup_error = exc
            if cleanup_error is not None and ambiguous_classified:
                cleanup_error = SpeechAmbiguousError()
            if cleanup_error is not None and success_committed:
                cleanup_error = SpeechProviderError(
                    SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE
                )
            if cleanup_error is not None:
                raise cleanup_error

    def _transition(
        self,
        token: SpeechAdmissionToken,
        state: TranscriptionState,
        *,
        media_type: str | None = None,
        byte_size: int | None = None,
        duration_ms: int | None = None,
    ) -> SpeechAdmissionToken:
        with self._uow_factory() as uow:
            updated = uow.speech_requests.transition(
                token,
                state.value,
                media_type=media_type,
                byte_size=byte_size,
                duration_ms=duration_ms,
            )
            uow.commit()
            return updated

    def _mark_dispatching(self, token: SpeechAdmissionToken) -> SpeechAdmissionToken:
        with self._uow_factory() as uow:
            updated = uow.speech_requests.mark_dispatching(token)
            uow.commit()
            return updated

    def _finalize(
        self,
        token: SpeechAdmissionToken,
        *,
        state: TranscriptionState,
        outcome_code: str | None = None,
        latency_ms: int | None = None,
    ) -> bool:
        try:
            with self._uow_factory() as uow:
                finalized = uow.speech_requests.finalize(
                    token,
                    state=state.value,
                    outcome_code=outcome_code,
                    latency_ms=latency_ms,
                )
                uow.commit()
                return finalized
        except SpeechLeaseStateError:
            return False

    async def _scan(
        self,
        request_id: str,
        path: Path,
        uploaded: UploadedSpeechFile,
        *,
        content_type: str,
        deadline: float,
    ) -> None:
        snapshot = self._scanner.audit_snapshot
        if (
            self._clock() + self._scanner.max_duration_seconds + _SCAN_DEADLINE_MARGIN_SECONDS
            >= deadline
        ):
            raise _StageFailure(
                SpeechErrorCode.TRANSCRIPTION_TIMEOUT,
                "scan_unavailable",
            )

        def scan() -> MalwareScanStatus:
            with path.open("rb") as stream:
                verdict = self._scanner.scan(
                    ReadOnlyMediaSource(
                        stream=stream,
                        byte_size=uploaded.byte_size,
                        streaming_sha256=uploaded.streaming_sha256,
                    )
                )
                return verdict.status

        started_at = datetime.now(UTC)
        try:
            status = await self._run_before_deadline(asyncio.to_thread(scan), deadline)
        except SpeechProviderError:
            raise
        except Exception:
            status = "error"
        finished_at = datetime.now(UTC)
        result = ScanResultKind.ERROR if status == "unknown" else ScanResultKind(status)
        if result is ScanResultKind.CLEAN:
            rejection_code = None
        elif status == "unknown":
            rejection_code = ScanRejectionCode.LEGACY_UNKNOWN_UNCLASSIFIED
        else:
            rejection_code = default_scan_rejection_code(result)
        audit_key = f"speech-scan:{request_id}"
        attempt = MediaScanAttempt(
            attempt_id=str(uuid5(NAMESPACE_URL, f"focusproof:{audit_key}")),
            artifact_sha256=uploaded.streaming_sha256,
            content_type=content_type[:255],
            scanner_backend=snapshot.scanner_backend,
            definitions_version=snapshot.definitions_version,
            definitions_fresh_at=snapshot.definitions_fresh_at,
            definitions_age_seconds=snapshot.definitions_age_seconds,
            max_bytes=snapshot.max_bytes,
            max_concurrent_scans=snapshot.max_concurrent_scans,
            deadline_ms=snapshot.deadline_ms,
            socket_timeout_ms=snapshot.socket_timeout_ms,
            scan_result=result,
            rejection_code=rejection_code,
            rejection_detail=None,
            started_at=started_at,
            finished_at=finished_at,
            idempotency_key=audit_key,
        )
        with self._uow_factory() as uow:
            uow.scan_audit.record_attempt(attempt)
            uow.commit()

        if status == "clean":
            return
        if status == "oversize":
            raise _StageFailure(
                SpeechErrorCode.AUDIO_TOO_LARGE,
                SpeechErrorCode.AUDIO_TOO_LARGE.value,
            )
        if status == "malicious":
            raise _StageFailure(SpeechErrorCode.INVALID_AUDIO, "malware_detected")
        if status == "timeout":
            raise _StageFailure(SpeechErrorCode.TRANSCRIPTION_TIMEOUT, "scan_unavailable")
        raise _StageFailure(
            SpeechErrorCode.TRANSCRIPTION_FAILED,
            "scan_unavailable",
        )

    async def _claim_asr_slot(
        self,
        *,
        request_id: str,
        deadline: float,
        release_deadline: float,
    ) -> ResourceSlotLease | None:
        while self._clock() < deadline:
            remaining = deadline - self._clock()
            with self._uow_factory() as uow:
                lease = uow.resource_slots.claim(
                    "asr",
                    work_kind="speech",
                    work_id=request_id,
                    lease_seconds=max(1, math.ceil(remaining + _CLEANUP_RESERVE_SECONDS)),
                    timeout_ms=max(1, math.ceil(remaining * 1000)),
                )
                uow.commit()
            if lease is not None:
                if self._clock() >= deadline:
                    self._release_slot(lease, deadline=release_deadline)
                    return None
                return lease
            await asyncio.sleep(min(_SLOT_POLL_SECONDS, max(0.0, remaining)))
        return None

    def _release_slot(
        self,
        lease: ResourceSlotLease,
        *,
        deadline: float,
    ) -> bool:
        with self._uow_factory() as uow:
            released = uow.resource_slots.release(
                lease,
                timeout_ms=max(1, math.ceil(max(0.0, deadline - self._clock()) * 1000)),
            )
            uow.commit()
            return released

    async def _cleanup(
        self,
        path: Path,
        lease: ResourceSlotLease | None,
        *,
        deadline: float,
    ) -> None:
        async def actions() -> None:
            if lease is not None and not self._release_slot(lease, deadline=deadline):
                raise RuntimeError("resource slot cleanup failed")
            await self._file_remover(path)

        cleanup_deadline = min(
            deadline,
            self._clock() + _CLEANUP_RESERVE_SECONDS,
        )
        task = asyncio.create_task(actions())
        cancelled = False
        try:
            while not task.done():
                remaining = cleanup_deadline - self._clock()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    async with asyncio.timeout(remaining):
                        await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
            if task.cancelled():
                raise RuntimeError("privacy cleanup was cancelled")
            error = task.exception()
            if error is not None:
                raise error
        except Exception:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_FAILED) from None
        if cancelled:
            raise asyncio.CancelledError

    async def _check_disconnect(self, probe: DisconnectProbe) -> None:
        if await probe():
            raise asyncio.CancelledError

    def _ensure_business_time(self, deadline: float) -> None:
        if self._clock() >= deadline:
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_TIMEOUT)

    @staticmethod
    async def _run_before_deadline(
        awaitable: Awaitable[_T],
        deadline: float,
    ) -> _T:
        try:
            async with asyncio.timeout_at(deadline):
                return await awaitable
        except TimeoutError:
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_TIMEOUT) from None

    @staticmethod
    async def _remove_file(path: Path) -> None:
        await asyncio.to_thread(path.unlink, missing_ok=True)
