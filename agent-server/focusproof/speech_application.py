from __future__ import annotations

import asyncio
import math
import os
import threading
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
from focusproof.persistence.unit_of_work import UnitOfWork, UnitOfWorkFactory
from focusproof.speech_core.errors import (
    SpeechAmbiguousError,
    SpeechError,
    SpeechErrorCode,
    SpeechProviderError,
)
from focusproof.speech_core.idempotency import request_fingerprint
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
_SLOT_RELEASE_WAIT_SECONDS = 0.1


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


class _DatabaseDeadlineError(SpeechProviderError):
    def __init__(self) -> None:
        super().__init__(SpeechErrorCode.TRANSCRIPTION_TIMEOUT)


class _DatabaseOperationCancelled(RuntimeError):
    pass


class _ResultCommitUnavailable(SpeechProviderError):
    def __init__(self) -> None:
        super().__init__(SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE)


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
        provider_result_received = False
        success_committed = False
        cleanup_error: SpeechProviderError | None = None
        ambiguous_classified = False
        try:
            await asyncio.to_thread(self._prepare_temp_directory)
            self._ensure_business_time(business_deadline)
            await self._check_disconnect(disconnect_probe)
            token = await self._transition(
                token,
                TranscriptionState.UPLOADING,
                deadline=business_deadline,
            )
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
            token = await self._transition(
                token,
                TranscriptionState.SCANNING,
                media_type=upload.declared_media_type,
                byte_size=uploaded.byte_size,
                request_fingerprint_value=request_fingerprint(
                    payload_sha256=uploaded.streaming_sha256,
                    language_hint=language_hint,
                    media_type=(upload.declared_media_type or "application/octet-stream"),
                ),
                deadline=business_deadline,
            )
            try:
                await self._scan(
                    token,
                    request_path,
                    uploaded,
                    content_type=upload.declared_media_type or "application/octet-stream",
                    cleanup_deadline=admission.deadline,
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
            token = await self._transition(
                token,
                TranscriptionState.INSPECTING,
                media_type=facts.media_type,
                byte_size=facts.byte_size,
                duration_ms=facts.duration_ms,
                deadline=business_deadline,
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
            token = await self._mark_dispatching(
                token,
                deadline=business_deadline,
                cleanup_deadline=admission.deadline,
            )
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
            provider_result_received = True
            latency_ms = max(0, round((self._clock() - provider_started) * 1000))
            if not await self._finalize(
                token,
                state=TranscriptionState.SUCCEEDED,
                latency_ms=latency_ms,
                deadline=business_deadline,
                cleanup_deadline=admission.deadline,
                cleanup_lease=asr_lease,
            ):
                raise _ResultCommitUnavailable()
            success_committed = True
            return result
        except asyncio.CancelledError:
            if dispatched:
                ambiguous_classified = True
                await self._finalize(
                    token,
                    state=TranscriptionState.AMBIGUOUS,
                    outcome_code=SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS.value,
                    deadline=admission.deadline,
                )
                raise SpeechAmbiguousError() from None
            await self._finalize(
                token,
                state=TranscriptionState.CANCELLED,
                outcome_code="client_cancelled",
                deadline=admission.deadline,
            )
            raise
        except SpeechAmbiguousError:
            ambiguous_classified = True
            await self._finalize(
                token,
                state=TranscriptionState.AMBIGUOUS,
                outcome_code=SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS.value,
                deadline=admission.deadline,
            )
            raise
        except SpeechError as exc:
            if not isinstance(exc, (_DatabaseDeadlineError, _ResultCommitUnavailable)):
                await self._finalize(
                    token,
                    state=TranscriptionState.FAILED_TERMINAL,
                    outcome_code=(
                        exc.outcome_code if isinstance(exc, _StageFailure) else exc.code.value
                    ),
                    deadline=admission.deadline,
                )
            raise
        except Exception:
            if dispatched:
                ambiguous_classified = True
                await self._finalize(
                    token,
                    state=TranscriptionState.AMBIGUOUS,
                    outcome_code=SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS.value,
                    deadline=admission.deadline,
                )
                raise SpeechAmbiguousError() from None
            await self._finalize(
                token,
                state=TranscriptionState.FAILED_TERMINAL,
                outcome_code=SpeechErrorCode.TRANSCRIPTION_FAILED.value,
                deadline=admission.deadline,
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
            if cleanup_error is not None and (success_committed or provider_result_received):
                cleanup_error = SpeechProviderError(
                    SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE
                )
            if cleanup_error is not None:
                raise cleanup_error

    async def _transition(
        self,
        token: SpeechAdmissionToken,
        state: TranscriptionState,
        *,
        media_type: str | None = None,
        byte_size: int | None = None,
        duration_ms: int | None = None,
        request_fingerprint_value: str | None = None,
        deadline: float,
    ) -> SpeechAdmissionToken:
        def operation(uow: UnitOfWork, timeout_ms: int) -> SpeechAdmissionToken:
            return uow.speech_requests.transition(
                token,
                state.value,
                media_type=media_type,
                byte_size=byte_size,
                duration_ms=duration_ms,
                request_fingerprint=request_fingerprint_value,
                timeout_ms=timeout_ms,
            )

        return await self._run_db_operation(
            operation,
            timeout_fallback=self._terminal_fallback(
                token,
                state=TranscriptionState.FAILED_TERMINAL,
                outcome_code=SpeechErrorCode.TRANSCRIPTION_TIMEOUT.value,
            ),
            deadline=deadline,
            cleanup_deadline=deadline + _CLEANUP_RESERVE_SECONDS,
        )

    async def _mark_dispatching(
        self,
        token: SpeechAdmissionToken,
        *,
        deadline: float,
        cleanup_deadline: float,
    ) -> SpeechAdmissionToken:
        def operation(uow: UnitOfWork, timeout_ms: int) -> SpeechAdmissionToken:
            return uow.speech_requests.mark_dispatching(
                token,
                timeout_ms=timeout_ms,
            )

        return await self._run_db_operation(
            operation,
            timeout_fallback=self._terminal_fallback(
                token,
                state=TranscriptionState.FAILED_TERMINAL,
                outcome_code=SpeechErrorCode.TRANSCRIPTION_TIMEOUT.value,
            ),
            deadline=deadline,
            cleanup_deadline=cleanup_deadline,
        )

    async def _finalize(
        self,
        token: SpeechAdmissionToken,
        *,
        state: TranscriptionState,
        outcome_code: str | None = None,
        latency_ms: int | None = None,
        deadline: float,
        cleanup_deadline: float | None = None,
        cleanup_lease: ResourceSlotLease | None = None,
    ) -> bool:

        def operation(uow: UnitOfWork, timeout_ms: int) -> bool:
            return uow.speech_requests.finalize(
                token,
                state=state.value,
                outcome_code=outcome_code,
                latency_ms=latency_ms,
                timeout_ms=timeout_ms,
            )

        fallback_state = state
        fallback_outcome = outcome_code
        if state is TranscriptionState.SUCCEEDED:
            fallback_state = TranscriptionState.AMBIGUOUS
            fallback_outcome = SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS.value
        try:
            return await self._run_db_operation(
                operation,
                timeout_fallback=self._terminal_fallback(
                    token,
                    cleanup_lease=cleanup_lease,
                    state=fallback_state,
                    outcome_code=fallback_outcome,
                    latency_ms=latency_ms if fallback_state is state else None,
                ),
                deadline=deadline,
                cleanup_deadline=cleanup_deadline or deadline,
            )
        except (_DatabaseDeadlineError, SpeechLeaseStateError):
            return False

    def _prepare_temp_directory(self) -> None:
        self._temp_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(self._temp_dir, flags)
        try:
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)

    def _terminal_fallback(
        self,
        token: SpeechAdmissionToken,
        *,
        state: TranscriptionState,
        outcome_code: str | None,
        latency_ms: int | None = None,
        cleanup_lease: ResourceSlotLease | None = None,
    ) -> Callable[[UnitOfWork, int], bool]:
        def finalize(uow: UnitOfWork, timeout_ms: int) -> bool:
            finalized = uow.speech_requests.finalize(
                token,
                state=state.value,
                outcome_code=outcome_code,
                latency_ms=latency_ms,
                timeout_ms=timeout_ms,
            )
            if cleanup_lease is not None:
                uow.resource_slots.release(
                    cleanup_lease,
                    timeout_ms=timeout_ms,
                )
            return finalized

        return finalize

    async def _run_db_operation(
        self,
        operation: Callable[[UnitOfWork, int], _T],
        *,
        timeout_fallback: Callable[[UnitOfWork, int], object],
        deadline: float,
        cleanup_deadline: float,
    ) -> _T:
        remaining = deadline - self._clock()
        if remaining <= 0:
            fallback_task = asyncio.create_task(
                asyncio.to_thread(
                    self._run_timeout_fallback,
                    timeout_fallback,
                    deadline=cleanup_deadline,
                )
            )
            fallback_task.add_done_callback(self._consume_background_task)
            raise _DatabaseDeadlineError()
        timeout_ms = max(1, math.ceil(remaining * 1000) - 25)
        abandoned = threading.Event()
        timed_out = threading.Event()

        def run() -> _T:
            try:
                with self._uow_factory() as uow:
                    result = operation(uow, timeout_ms)
                    if abandoned.is_set():
                        raise _DatabaseOperationCancelled
                    uow.commit()
                    return result
            except _DatabaseOperationCancelled:
                if timed_out.is_set():
                    self._run_timeout_fallback(
                        timeout_fallback,
                        deadline=cleanup_deadline,
                    )
                raise

        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            async with asyncio.timeout(remaining):
                return await asyncio.shield(task)
        except TimeoutError:
            timed_out.set()
            abandoned.set()
            task.add_done_callback(self._consume_background_task)
            raise _DatabaseDeadlineError() from None
        except asyncio.CancelledError:
            abandoned.set()
            task.add_done_callback(self._consume_background_task)
            raise

    def _run_timeout_fallback(
        self,
        fallback: Callable[[UnitOfWork, int], object],
        *,
        deadline: float,
    ) -> None:
        remaining = deadline - self._clock()
        if remaining <= 0:
            return
        try:
            with self._uow_factory() as uow:
                fallback(uow, max(1, math.ceil(remaining * 1000)))
                uow.commit()
        except (SpeechLeaseStateError, RuntimeError):
            return

    @staticmethod
    def _consume_background_task(task: asyncio.Task[object]) -> None:
        try:
            task.exception()
        except BaseException:
            pass

    async def _await_scan_fence(
        self,
        task: asyncio.Task[MalwareScanStatus],
        *,
        deadline: float,
    ) -> None:
        while not task.done():
            remaining = deadline - self._clock()
            if remaining <= 0:
                return
            try:
                async with asyncio.timeout(remaining):
                    await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                return

    async def _scan(
        self,
        token: SpeechAdmissionToken,
        path: Path,
        uploaded: UploadedSpeechFile,
        *,
        content_type: str,
        deadline: float,
        cleanup_deadline: float,
    ) -> None:
        snapshot = self._scanner.audit_snapshot
        request_id = token.request_id
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
        scan_task = asyncio.create_task(asyncio.to_thread(scan))
        try:
            status = await self._run_before_deadline(
                asyncio.shield(scan_task),
                deadline,
            )
        except asyncio.CancelledError:
            await self._await_scan_fence(scan_task, deadline=cleanup_deadline)
            raise
        except SpeechProviderError:
            await self._await_scan_fence(scan_task, deadline=cleanup_deadline)
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

        def record_attempt(uow: UnitOfWork, timeout_ms: int) -> None:
            del timeout_ms
            uow.scan_audit.record_attempt(attempt)

        await self._run_db_operation(
            record_attempt,
            timeout_fallback=self._terminal_fallback(
                token,
                state=TranscriptionState.FAILED_TERMINAL,
                outcome_code="scan_unavailable",
            ),
            deadline=deadline,
            cleanup_deadline=cleanup_deadline,
        )

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
                    await self._release_slot(lease, deadline=release_deadline)
                    return None
                return lease
            await asyncio.sleep(min(_SLOT_POLL_SECONDS, max(0.0, remaining)))
        return None

    async def _release_slot(
        self,
        lease: ResourceSlotLease,
        *,
        deadline: float,
    ) -> bool:
        remaining = max(0.0, deadline - self._clock())

        def release() -> bool:
            with self._uow_factory() as uow:
                released = uow.resource_slots.release(
                    lease,
                    timeout_ms=max(1, math.ceil(remaining * 1000)),
                )
                uow.commit()
                return released

        task = asyncio.create_task(asyncio.to_thread(release))
        if remaining <= 0:
            task.add_done_callback(self._consume_background_task)
            return False
        try:
            async with asyncio.timeout(min(remaining, _SLOT_RELEASE_WAIT_SECONDS)):
                return await asyncio.shield(task)
        except TimeoutError:
            task.add_done_callback(self._consume_background_task)
            return False

    async def _cleanup(
        self,
        path: Path,
        lease: ResourceSlotLease | None,
        *,
        deadline: float,
    ) -> None:
        async def actions() -> None:
            released = lease is None or await self._release_slot(lease, deadline=deadline)
            await self._file_remover(path)
            if not released:
                raise RuntimeError("resource slot cleanup failed")

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
