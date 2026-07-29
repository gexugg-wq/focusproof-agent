from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
from typing import Final, Literal


MAINTENANCE_LOCK_NAME: Final = ".focusproof-maintenance.lock"
OPERATIONS_LOGGER = logging.getLogger("focusproof.operations")
RecoveryOperation = Literal["backup", "restore"]


class MaintenanceLockError(RuntimeError):
    pass


@contextmanager
def recovery_outcome(operation: RecoveryOperation) -> Iterator[None]:
    try:
        yield
    except BaseException:
        _emit_recovery_outcome(operation, "failed")
        raise
    else:
        _emit_recovery_outcome(operation, "completed")


def _emit_recovery_outcome(
    operation: RecoveryOperation,
    outcome: Literal["completed", "failed"],
) -> None:
    OPERATIONS_LOGGER.info(
        json.dumps(
            {"event": "recovery", "operation": operation, "outcome": outcome},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def maintenance_lock_path(data_dir: Path) -> Path:
    return data_dir / MAINTENANCE_LOCK_NAME


def is_maintenance_locked(data_dir: Path) -> bool:
    path = maintenance_lock_path(data_dir)
    return path.is_file() and not path.is_symlink()


@contextmanager
def maintenance_lock(data_dir: Path) -> Iterator[Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    if data_dir.is_symlink():
        raise MaintenanceLockError("maintenance data path must not be a symlink")
    path = maintenance_lock_path(data_dir)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise MaintenanceLockError("maintenance lock is already held") from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        yield path
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
