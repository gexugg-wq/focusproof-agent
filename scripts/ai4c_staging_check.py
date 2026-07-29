from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import logging
from pathlib import Path
from typing import Final, Literal

from focusproof.recovery import (
    MAINTENANCE_MARKER_NAME,
    RecoveryCoordinationError,
    is_maintenance_locked,
    is_recovery_incomplete,
    maintenance_window,
    writer_barrier,
)

MAINTENANCE_LOCK_NAME: Final = MAINTENANCE_MARKER_NAME
OPERATIONS_LOGGER = logging.getLogger("focusproof.operations")
RecoveryOperation = Literal["backup", "restore"]
__all__ = (
    "MAINTENANCE_LOCK_NAME",
    "MaintenanceLockError",
    "is_maintenance_locked",
    "is_recovery_incomplete",
    "maintenance_lock",
    "maintenance_window",
    "recovery_outcome",
    "writer_barrier",
)


MaintenanceLockError = RecoveryCoordinationError


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


@contextmanager
def maintenance_lock(data_dir: Path) -> Iterator[Path]:
    with maintenance_window(data_dir) as window:
        yield window.maintenance_path
