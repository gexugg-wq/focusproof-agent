from __future__ import annotations

from collections.abc import Callable
from math import ceil
import threading
from time import monotonic, sleep
from typing import TypeVar
from uuid import uuid4

from focusproof.contracts.media_scan import MediaScanAuditSnapshot
from focusproof.media_core.ingestion import (
    MalwareDetectedError,
    MalwareScanFailedError,
    MalwareScanTimeoutError,
    MalwareScanUnavailableError as CoreMalwareScanUnavailableError,
    MalwareScanUnknownError,
)
from focusproof.media_core.ports import (
    MalwareScanner,
    MalwareScanVerdict,
    MediaValidator,
    ReadOnlyMediaSource,
    ValidatedMediaMetadata,
)
from focusproof.persistence.repositories import ResourceSlotLease
from focusproof.persistence.unit_of_work import UnitOfWorkFactory


_T = TypeVar("_T")
_RESOURCE_SLOT_RELEASE_TIMEOUT_SECONDS = 2.0


def _remaining_timeout_ms(deadline: float) -> int:
    return max(1, ceil(max(0.0, deadline - monotonic()) * 1000))


class ResourceSlotController:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        lease_seconds: int,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("resource slot lease must be positive")
        self._uow_factory = uow_factory
        self._lease_seconds = lease_seconds

    def reconcile(self, *, configured_count: int, config_generation: int) -> None:
        with self._uow_factory() as uow:
            uow.resource_slots.reconcile(
                "scan",
                configured_count=configured_count,
                config_generation=config_generation,
            )
            uow.commit()

    def claim(
        self,
        *,
        work_kind: str,
        work_id: str,
        deadline: float,
    ) -> ResourceSlotLease | None:
        while deadline > monotonic():
            with self._uow_factory() as uow:
                lease = uow.resource_slots.claim(
                    "scan",
                    work_kind=work_kind,
                    work_id=work_id,
                    timeout_ms=_remaining_timeout_ms(deadline),
                    lease_seconds=self._lease_seconds,
                )
                uow.commit()
            if lease is not None:
                if deadline <= monotonic():
                    self.release(lease, deadline=deadline)
                    return None
                return lease
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            sleep(min(0.01, remaining))
        return None

    def release(
        self,
        lease: ResourceSlotLease,
        *,
        deadline: float | None = None,
    ) -> bool:
        with self._uow_factory() as uow:
            released = uow.resource_slots.release(
                lease,
                timeout_ms=(_remaining_timeout_ms(deadline) if deadline is not None else None),
            )
            uow.commit()
            return released


class SlotBoundMalwareScanner:
    def __init__(
        self,
        scanner: MalwareScanner,
        controller: ResourceSlotController,
        *,
        work_kind: str,
    ) -> None:
        if work_kind not in {"image", "speech"}:
            raise ValueError("scan work kind is invalid")
        self._scanner = scanner
        self._controller = controller
        self._work_kind = work_kind

    @property
    def audit_snapshot(self) -> MediaScanAuditSnapshot:
        return self._scanner.audit_snapshot

    @property
    def max_duration_seconds(self) -> float:
        scanner_seconds = self.audit_snapshot.deadline_ms / 1000
        return (2 * scanner_seconds) + _RESOURCE_SLOT_RELEASE_TIMEOUT_SECONDS

    def scan(self, source: ReadOnlyMediaSource) -> MalwareScanVerdict:
        snapshot = self.audit_snapshot
        deadline = monotonic() + snapshot.deadline_ms / 1000
        lease = self._controller.claim(
            work_kind=self._work_kind,
            work_id=f"{self._work_kind}-{uuid4().hex}",
            deadline=deadline,
        )
        if lease is None:
            return MalwareScanVerdict(status="timeout", engine=snapshot.scanner_backend)
        try:
            verdict = self._scanner.scan(source)
        finally:
            released = self._controller.release(
                lease,
                deadline=monotonic() + _RESOURCE_SLOT_RELEASE_TIMEOUT_SECONDS,
            )
        if not released:
            return MalwareScanVerdict(status="error", engine=snapshot.scanner_backend)
        return verdict


class MediaUploadCancelled(BaseException):
    pass


class ThreadSafeMediaCancellationGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._committed = False

    def cancel(self) -> bool:
        with self._lock:
            if self._committed:
                return False
            self._cancelled = True
            return True

    def check_active(self) -> None:
        with self._lock:
            if self._cancelled:
                raise MediaUploadCancelled

    def run_if_active(self, action: Callable[[], _T]) -> _T:
        with self._lock:
            if self._cancelled:
                raise MediaUploadCancelled
            return action()

    def run_commit(self, action: Callable[[], _T]) -> _T:
        with self._lock:
            if self._cancelled:
                raise MediaUploadCancelled
            result = action()
            self._committed = True
            return result


class MediaDisabledError(RuntimeError):
    pass


class MediaMaliciousError(ValueError):
    pass


class MediaScanUnavailableError(RuntimeError):
    pass


def map_malware_scan_error(exc: Exception) -> Exception:
    if isinstance(exc, MalwareDetectedError):
        return MediaMaliciousError("malicious media")
    if isinstance(
        exc,
        (
            CoreMalwareScanUnavailableError,
            MalwareScanTimeoutError,
            MalwareScanFailedError,
            MalwareScanUnknownError,
        ),
    ):
        return MediaScanUnavailableError("media scan unavailable")
    return exc


class UnsupportedMediaError(ValueError):
    """The uploaded source is not a supported, valid image."""


class MediaSourceTooLargeError(ValueError):
    """The uploaded source exceeded the original-byte product limit."""


class MediaValidationBoundary:
    """Convert only validator-originated input failures to an application error."""

    def __init__(self, validator: MediaValidator) -> None:
        self._validator = validator

    def validate(
        self,
        source: ReadOnlyMediaSource,
        declared_media_type: str | None,
    ) -> ValidatedMediaMetadata:
        try:
            return self._validator.validate(source, declared_media_type)
        except ValueError as exc:
            raise UnsupportedMediaError("unsupported media") from exc
