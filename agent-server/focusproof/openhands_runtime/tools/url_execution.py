from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import BoundedSemaphore, Lock
from typing import TypeVar


T = TypeVar("T")


class UrlExecutionBusyError(RuntimeError):
    """Raised when the bounded URL isolation capacity is exhausted."""


class UrlExecutionPoolClosedError(RuntimeError):
    """Raised when work is submitted after application shutdown."""


class BoundedUrlExecutionPool:
    """Bound blocking URL I/O without replacing OpenHands tool dispatch."""

    def __init__(self, *, max_workers: int = 4, max_pending: int = 4) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if max_pending < 0:
            raise ValueError("max_pending must not be negative")
        self._max_in_flight = max_workers + max_pending
        self._capacity = BoundedSemaphore(self._max_in_flight)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="focusproof-url-worker",
        )
        self._lock = Lock()
        self._closed = False
        self._in_flight = 0
        self._submitted_count = 0

    @property
    def max_in_flight(self) -> int:
        return self._max_in_flight

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    @property
    def submitted_count(self) -> int:
        with self._lock:
            return self._submitted_count

    def submit(self, operation: Callable[[], T]) -> Future[T]:
        if not self._capacity.acquire(blocking=False):
            raise UrlExecutionBusyError("URL verification capacity is exhausted")
        with self._lock:
            if self._closed:
                self._capacity.release()
                raise UrlExecutionPoolClosedError("URL verification pool is closed")
            self._in_flight += 1
            self._submitted_count += 1
            try:
                future = self._executor.submit(operation)
            except BaseException:
                self._in_flight -= 1
                self._capacity.release()
                raise

        def release_capacity(completed: Future[T]) -> None:
            del completed
            self._release_capacity()

        future.add_done_callback(release_capacity)
        return future

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _release_capacity(self) -> None:
        with self._lock:
            self._in_flight -= 1
        self._capacity.release()
