from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from focusproof.api.auth import VerifiedIdentity, resolve_verified_identity
from focusproof.api.oidc import IdentityUnavailableError, InvalidTokenError
from focusproof.api.request_limits import (
    is_speech_upload_scope,
    speech_upload_session_id,
)
from focusproof.api.speech_models import speech_error_http
from focusproof.persistence.providers import PrincipalDisabledError
from focusproof.persistence.repositories import (
    SpeechAdmissionToken,
    SpeechHmacReadinessError,
    SpeechLeaseStateError,
    SpeechQuotaExceededError,
)
from focusproof.speech_application import SpeechExecutionAdmission
from focusproof.speech_core.errors import SpeechAdmissionError, SpeechErrorCode

_T = TypeVar("_T")
_LOGGER = logging.getLogger("focusproof.speech.recovery")
IdentityResolver = Callable[..., Awaitable[VerifiedIdentity]]
Fence = Callable[[], Awaitable[None]]


class _SpeechClientDisconnected(Exception):
    pass


def _response(status: int, code: str, *, retryable: bool = False) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(
        status_code=status,
        content={"code": code, "retryable": retryable},
        headers=headers,
    )


class SpeechAdmissionGate:
    def __init__(self, *, opened: bool = False) -> None:
        self._open = opened

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False


class SpeechAdmissionMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        application: FastAPI,
        gate: SpeechAdmissionGate,
        identity_resolver: IdentityResolver = resolve_verified_identity,
        clock: Callable[[], float] = time.monotonic,
        total_timeout_seconds: float = 120.0,
        cleanup_reserve_seconds: float = 5.0,
    ) -> None:
        if total_timeout_seconds <= 0:
            raise ValueError("speech request timeout must be positive")
        if not 0 < cleanup_reserve_seconds < total_timeout_seconds:
            raise ValueError("speech cleanup reserve must fit inside request timeout")
        self._app = app
        self._application = application
        self._gate = gate
        self._identity_resolver = identity_resolver
        self._clock = clock
        self._total_timeout_seconds = total_timeout_seconds
        self._cleanup_reserve_seconds = cleanup_reserve_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not is_speech_upload_scope(self._application, scope)
        ):
            await self._app(scope, receive, send)
            return
        capability = getattr(self._application.state, "speech_capability", None)
        if not isinstance(capability, dict) or capability.get("enabled") is not True:
            await _response(503, "speech_disabled")(scope, receive, send)
            return

        entry_deadline = self._clock() + self._total_timeout_seconds
        business_deadline = entry_deadline - self._cleanup_reserve_seconds
        if not self._gate.is_open:
            await _response(
                503,
                "transcription_provider_unavailable",
                retryable=True,
            )(scope, receive, send)
            return
        request = Request(scope, receive)
        try:
            identity = await self._identity_resolver(
                request,
                authorization=Headers(scope=scope).get("authorization"),
            )
        except InvalidTokenError:
            await _response(401, "invalid_token")(scope, receive, send)
            return
        except PrincipalDisabledError:
            await _response(403, "forbidden")(scope, receive, send)
            return
        except IdentityUnavailableError:
            await _response(503, "identity_unavailable")(scope, receive, send)
            return
        except SQLAlchemyError:
            await _response(503, "database_unavailable", retryable=True)(scope, receive, send)
            return
        raw_key = Headers(scope=scope).get("idempotency-key")
        try:
            if raw_key is None:
                raise ValueError
            UUID(raw_key)
        except (ValueError, AttributeError):
            await _response(422, "invalid_idempotency_key")(scope, receive, send)
            return
        try:
            session_id = speech_upload_session_id(self._application, scope)
            if session_id is None:
                await _response(404, "speech_session_unavailable")(scope, receive, send)
                return

            uow_factory = self._application.state.uow_factory
            with uow_factory() as uow:
                token = uow.speech_requests.admit(
                    owner_user_id=identity.verified_user_id,
                    session_id=session_id,
                    idempotency_key=raw_key,
                    request_fingerprint=None,
                    lease_owner=f"speech-{uuid4()}",
                    lease_seconds=120,
                )
                uow.commit()
        except SpeechQuotaExceededError:
            await _response(429, SpeechErrorCode.TRANSCRIPTION_RATE_LIMITED.value, retryable=True)(
                scope, receive, send
            )
            return
        except SpeechAdmissionError as exc:
            if exc.code is SpeechErrorCode.TRANSCRIPTION_FAILED:
                await _response(404, "speech_session_unavailable")(scope, receive, send)
            else:
                status, retryable = speech_error_http(exc.code)
                await _response(
                    status,
                    exc.code.value,
                    retryable=retryable,
                )(scope, receive, send)
            return
        except SpeechHmacReadinessError:
            await _response(503, "speech_disabled")(scope, receive, send)
            return
        except (SQLAlchemyError, RuntimeError):
            await _response(503, "database_unavailable", retryable=True)(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state["verified_identity"] = identity
        state["speech_admission"] = SpeechExecutionAdmission(
            token=token,
            deadline=entry_deadline,
        )
        registry = getattr(self._application.state, "speech_task_registry", None)
        if not isinstance(registry, SpeechTaskRegistry):
            await self._finalize_admission(
                token,
                state="failed_terminal",
                outcome_code="transcription_provider_unavailable",
                deadline=entry_deadline,
            )
            await _response(
                503,
                "transcription_provider_unavailable",
                retryable=True,
            )(scope, receive, send)
            return

        response_started = False

        async def deadline_receive() -> Any:
            remaining = business_deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                message = await receive()
            if message["type"] == "http.disconnect":
                raise _SpeechClientDisconnected
            return message

        async def tracked_send(message: Any) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        async def run_downstream() -> None:
            await self._app(scope, deadline_receive, tracked_send)

        task: asyncio.Task[None] = registry.create_task(run_downstream())
        try:
            remaining = business_deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                await asyncio.shield(task)
            # The service finalizes successful and handled execution paths with a
            # newer lease generation.  A normal return that still owns the
            # admission token is therefore an early rejection (multipart
            # validation, body overflow, or unavailable composition).  The CAS
            # makes this fallback a no-op after a real service finalization.
            await self._finalize_admission(
                token,
                state="failed_terminal",
                outcome_code="upload_failed",
                deadline=entry_deadline,
            )
        except _SpeechClientDisconnected:
            await self._cancel_and_finalize(
                task,
                token=token,
                state="cancelled",
                outcome_code="client_cancelled",
                deadline=entry_deadline,
            )
        except TimeoutError:
            await self._cancel_and_finalize(
                task,
                token=token,
                state="failed_terminal",
                outcome_code="transcription_timeout",
                deadline=entry_deadline,
            )
            if not response_started:
                await _response(504, "transcription_timeout", retryable=True)(scope, receive, send)
        except asyncio.CancelledError:
            await self._cancel_and_finalize(
                task,
                token=token,
                state="cancelled",
                outcome_code="client_cancelled",
                deadline=entry_deadline,
            )
            raise
        except Exception:
            await self._finalize_admission(
                token,
                state="failed_terminal",
                outcome_code="upload_failed",
                deadline=entry_deadline,
            )
            raise

    async def _cancel_and_finalize(
        self,
        task: asyncio.Task[Any],
        *,
        token: SpeechAdmissionToken,
        state: str,
        outcome_code: str,
        deadline: float,
    ) -> None:
        task.cancel()
        await self._finalize_admission(
            token,
            state=state,
            outcome_code=outcome_code,
            deadline=deadline,
        )
        remaining = deadline - self._clock()
        if remaining <= 0:
            return
        try:
            async with asyncio.timeout(remaining):
                await task
        except BaseException:
            pass

    async def _finalize_admission(
        self,
        token: SpeechAdmissionToken,
        *,
        state: str,
        outcome_code: str,
        deadline: float,
    ) -> None:
        def finalize() -> None:
            with self._application.state.uow_factory() as uow:
                uow.speech_requests.finalize(
                    token,
                    state=state,
                    outcome_code=outcome_code,
                )
                uow.commit()

        remaining = deadline - self._clock()
        if remaining <= 0:
            return
        try:
            async with asyncio.timeout(remaining):
                await asyncio.to_thread(finalize)
        except (
            TimeoutError,
            SpeechLeaseStateError,
            SQLAlchemyError,
            RuntimeError,
        ):
            return


class SpeechTaskRegistry:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    def create_task(self, awaitable: Coroutine[Any, Any, _T]) -> asyncio.Task[_T]:
        if self._closed:
            raise RuntimeError("speech task registry is closed")
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def close(
        self,
        *,
        gate: SpeechAdmissionGate,
        grace_seconds: float,
        fence: Fence,
    ) -> None:
        gate.close()
        self._closed = True
        loop = asyncio.get_running_loop()
        shutdown_deadline = loop.time() + max(0.0, grace_seconds)
        pending = set(self._tasks)
        if pending and grace_seconds > 0:
            _, pending = await asyncio.wait(
                pending,
                timeout=grace_seconds / 2,
            )
        for task in pending:
            task.cancel()
        if pending:
            task_deadline = shutdown_deadline - (max(0.0, grace_seconds) / 4)
            remaining = task_deadline - loop.time()
            if remaining > 0:
                await asyncio.wait(pending, timeout=remaining)
        stubborn = {task for task in pending if not task.done()}
        for task in stubborn:
            task.cancel()
        if stubborn:
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        async def run_fence() -> None:
            await fence()

        fence_task: asyncio.Task[None] = asyncio.create_task(run_fence())
        remaining = shutdown_deadline - loop.time()
        if remaining > 0:
            await asyncio.wait({fence_task}, timeout=remaining)
        else:
            await asyncio.sleep(0)
        if fence_task.done():
            await asyncio.gather(fence_task, return_exceptions=True)
        else:
            fence_task.cancel()
            await asyncio.sleep(0)
            if not fence_task.done():
                fence_task.cancel()
            await asyncio.sleep(0)
            if fence_task.done():
                await asyncio.gather(fence_task, return_exceptions=True)


class SpeechRecoveryCounters:
    __slots__ = ("expired_leases", "resource_slot_sweeps", "stale_temp_files")

    def __init__(
        self,
        *,
        expired_leases: int,
        resource_slot_sweeps: int,
        stale_temp_files: int,
    ) -> None:
        self.expired_leases = expired_leases
        self.resource_slot_sweeps = resource_slot_sweeps
        self.stale_temp_files = stale_temp_files


class SpeechRecoverySweeper:
    def __init__(
        self,
        *,
        uow_factory: Any,
        temp_dir: Path,
        stale_after_seconds: float,
        interval_seconds: float,
    ) -> None:
        if not temp_dir.is_absolute():
            raise ValueError("speech temp directory must be absolute")
        self._uow_factory = uow_factory
        self._temp_dir = temp_dir
        self._stale_after_seconds = stale_after_seconds
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def recover_once(self, *, now: datetime | None = None) -> SpeechRecoveryCounters:
        actual_now = now or datetime.now(UTC)
        counters = await asyncio.to_thread(self._recover_sync, actual_now)
        _LOGGER.info(
            "speech recovery completed expired_leases=%d resource_slot_sweeps=%d "
            "stale_temp_files=%d",
            counters.expired_leases,
            counters.resource_slot_sweeps,
            counters.stale_temp_files,
        )
        return counters

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _recover_sync(self, now: datetime) -> SpeechRecoveryCounters:
        with self._uow_factory() as uow:
            expired = uow.speech_requests.recover_expired(now=now)
            resource_slot_sweeps = 0
            for resource_kind in ("scan", "asr"):
                lease = uow.resource_slots.claim(
                    resource_kind,
                    work_kind="speech",
                    work_id=f"speech-recovery-{uuid4().hex}",
                    lease_seconds=1,
                    now=now,
                )
                if lease is not None:
                    uow.resource_slots.release(lease)
                resource_slot_sweeps += 1
            uow.commit()
        stale = 0
        cutoff = now.timestamp() - self._stale_after_seconds
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        for candidate in self._temp_dir.iterdir():
            if (
                candidate.suffix not in {".audio", ".wav", ".mp3", ".webm"}
                or candidate.is_symlink()
            ):
                continue
            try:
                UUID(candidate.stem)
                details = candidate.stat()
            except (ValueError, OSError):
                continue
            if not candidate.is_file() or details.st_mtime > cutoff:
                continue
            try:
                candidate.unlink()
            except OSError:
                continue
            stale += 1
        return SpeechRecoveryCounters(
            expired_leases=expired,
            resource_slot_sweeps=resource_slot_sweeps,
            stale_temp_files=stale,
        )

    async def _run(self) -> None:
        while True:
            try:
                await self.recover_once()
            except Exception:
                _LOGGER.warning("speech recovery sweep failed")
            await asyncio.sleep(self._interval_seconds)
