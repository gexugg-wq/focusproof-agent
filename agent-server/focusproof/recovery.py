from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Final, Iterator

from filelock import FileLock, Timeout


COORDINATION_SUFFIX: Final = ".focusproof-recovery"
MAINTENANCE_MARKER_NAME: Final = ".focusproof-maintenance.lock"
RECOVERY_INCOMPLETE_MARKER_NAME: Final = ".focusproof-recovery-incomplete"
ADMISSION_LOCK_NAME: Final = "writer-admission.lock"
WRITER_BARRIER_LOCK_NAME: Final = "writer-drain.lock"
DEFAULT_BARRIER_TIMEOUT_SECONDS: Final = 300.0


class RecoveryCoordinationError(RuntimeError):
    """The recovery coordination state is unsafe or unavailable."""


class WriterBlockedError(RecoveryCoordinationError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def coordination_dir(data_dir: Path) -> Path:
    root = coordination_dir_path(data_dir)
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise RecoveryCoordinationError("recovery coordination path is a symlink")
    root.mkdir(mode=0o700, exist_ok=True)
    if not root.is_dir():
        raise RecoveryCoordinationError("recovery coordination path is not a directory")
    return root


def coordination_dir_path(data_dir: Path) -> Path:
    absolute = Path(os.path.abspath(data_dir))
    return absolute.parent / f".{absolute.name}{COORDINATION_SUFFIX}"


def maintenance_marker_path(data_dir: Path) -> Path:
    return coordination_dir(data_dir) / MAINTENANCE_MARKER_NAME


def recovery_incomplete_marker_path(data_dir: Path) -> Path:
    return coordination_dir(data_dir) / RECOVERY_INCOMPLETE_MARKER_NAME


def is_maintenance_locked(data_dir: Path) -> bool:
    return _is_regular_marker(maintenance_marker_path(data_dir))


def is_recovery_incomplete(data_dir: Path) -> bool:
    return _is_regular_marker(recovery_incomplete_marker_path(data_dir))


def writer_block_code(data_dir: Path) -> str | None:
    if is_recovery_incomplete(data_dir):
        return "recovery_incomplete"
    if is_maintenance_locked(data_dir):
        return "maintenance_mode"
    return None


@contextmanager
def writer_barrier(
    data_dir: Path,
    *,
    timeout_seconds: float = DEFAULT_BARRIER_TIMEOUT_SECONDS,
) -> Iterator[None]:
    root = coordination_dir(data_dir)
    admission = _lock(root / ADMISSION_LOCK_NAME, timeout_seconds)
    barrier = _lock(root / WRITER_BARRIER_LOCK_NAME, timeout_seconds)
    acquired = False
    try:
        with admission:
            _raise_if_writes_blocked(data_dir)
            barrier.acquire()
            acquired = True
        _raise_if_writes_blocked(data_dir)
        yield
    except Timeout as exc:
        raise RecoveryCoordinationError("writer barrier timed out") from exc
    finally:
        if acquired:
            barrier.release()


@dataclass(slots=True)
class MaintenanceWindow:
    data_dir: Path
    maintenance_path: Path
    _recovery_started: bool = False
    _recovery_verified: bool = False

    def begin_recovery(self) -> None:
        _create_marker(
            recovery_incomplete_marker_path(self.data_dir),
            allow_existing=True,
        )
        self._recovery_started = True

    def complete_recovery(self) -> None:
        if not self._recovery_started:
            raise RecoveryCoordinationError("recovery was not started")
        self._recovery_verified = True


@contextmanager
def maintenance_window(
    data_dir: Path,
    *,
    timeout_seconds: float = DEFAULT_BARRIER_TIMEOUT_SECONDS,
) -> Iterator[MaintenanceWindow]:
    root = coordination_dir(data_dir)
    admission = _lock(root / ADMISSION_LOCK_NAME, timeout_seconds)
    barrier = _lock(root / WRITER_BARRIER_LOCK_NAME, timeout_seconds)
    marker = root / MAINTENANCE_MARKER_NAME
    window = MaintenanceWindow(data_dir=data_dir, maintenance_path=marker)
    barrier_acquired = False
    marker_created = False
    try:
        with admission:
            _create_marker(marker)
            marker_created = True
        barrier.acquire()
        barrier_acquired = True
        yield window
    except Timeout as exc:
        raise RecoveryCoordinationError("maintenance drain timed out") from exc
    finally:
        if marker_created:
            try:
                with admission:
                    _remove_marker(marker)
                    if window._recovery_started and window._recovery_verified:
                        _remove_marker(recovery_incomplete_marker_path(data_dir))
            finally:
                if barrier_acquired:
                    barrier.release()


def _lock(path: Path, timeout_seconds: float) -> FileLock:
    return FileLock(
        path,
        timeout=timeout_seconds,
        thread_local=False,
    )


def _raise_if_writes_blocked(data_dir: Path) -> None:
    code = writer_block_code(data_dir)
    if code is not None:
        raise WriterBlockedError(code)


def _is_regular_marker(path: Path) -> bool:
    if path.is_symlink():
        raise RecoveryCoordinationError("recovery marker is a symlink")
    return path.is_file()


def _create_marker(path: Path, *, allow_existing: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        if allow_existing and _is_regular_marker(path):
            return
        raise RecoveryCoordinationError("maintenance lock is already held") from exc
    try:
        os.write(descriptor, b"focusproof-recovery-v1\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_marker(path: Path) -> None:
    if path.is_symlink():
        raise RecoveryCoordinationError("recovery marker is a symlink")
    try:
        path.unlink()
    except FileNotFoundError:
        pass
