from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, Protocol

from filelock import FileLock, Timeout

_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class SessionBusyError(RuntimeError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session {session_id} is busy")
        self.session_id = session_id


class SessionRunLock(Protocol):
    def acquire(self, session_id: str) -> ContextManager[None]: ...


class FileSessionRunLock:
    def __init__(self, data_dir: Path, *, timeout_seconds: float) -> None:
        self._lock_dir = (data_dir.resolve() / "locks").resolve()
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        self._timeout_seconds = timeout_seconds

    @contextmanager
    def acquire(self, session_id: str) -> Iterator[None]:
        if not _SAFE_SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("session_id contains unsafe path characters")
        lock_path = (self._lock_dir / f"{session_id}.lock").resolve()
        if not lock_path.is_relative_to(self._lock_dir):
            raise ValueError("session lock path is unsafe")
        lock = FileLock(lock_path, timeout=self._timeout_seconds)
        try:
            with lock:
                yield
        except Timeout as exc:
            raise SessionBusyError(session_id) from exc
