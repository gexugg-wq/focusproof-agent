from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore
from typing import ContextManager, Protocol

from focusproof.openhands_runtime.factory import RuntimeUnavailableError


class ProviderAdmissionUnavailableError(RuntimeUnavailableError):
    """Raised before a paid provider run when bounded capacity is unavailable."""


class ProviderAdmission(Protocol):
    def acquire(self) -> ContextManager[None]: ...


class BoundedProviderAdmission:
    def __init__(
        self,
        *,
        max_concurrent: int,
        acquire_timeout_seconds: float,
    ) -> None:
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be greater than zero")
        if acquire_timeout_seconds <= 0:
            raise ValueError("acquire_timeout_seconds must be greater than zero")
        self._semaphore = BoundedSemaphore(max_concurrent)
        self._acquire_timeout_seconds = acquire_timeout_seconds

    @contextmanager
    def acquire(self) -> Iterator[None]:
        acquired = self._semaphore.acquire(timeout=self._acquire_timeout_seconds)
        if not acquired:
            raise ProviderAdmissionUnavailableError(
                "Real LLM provider admission capacity is unavailable"
            )
        try:
            yield
        finally:
            self._semaphore.release()
