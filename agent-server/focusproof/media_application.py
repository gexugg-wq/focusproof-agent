from __future__ import annotations

from collections.abc import Callable
import threading
from typing import TypeVar

from focusproof.media_core.ingestion import (
    MalwareDetectedError,
    MalwareScanFailedError,
    MalwareScanTimeoutError,
    MalwareScanUnavailableError as CoreMalwareScanUnavailableError,
    MalwareScanUnknownError,
)
from focusproof.media_core.ports import (
    MediaValidator,
    ReadOnlyMediaSource,
    ValidatedMediaMetadata,
)


_T = TypeVar("_T")


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
