from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import math


@dataclass(frozen=True, slots=True)
class ClamdLimits:
    max_bytes: int
    max_concurrent_scans: int
    deadline_ms: int
    socket_timeout_ms: int
    admission_timeout_ms: int
    definitions_version: str
    definitions_fresh_at: datetime

    def __post_init__(self) -> None:
        if not self.definitions_version.strip():
            raise ValueError("definitions_version must not be blank")
        if self.definitions_fresh_at.tzinfo is None:
            raise ValueError("definitions_fresh_at must be timezone-aware")
        for field_name in (
            "max_bytes",
            "max_concurrent_scans",
            "deadline_ms",
            "socket_timeout_ms",
            "admission_timeout_ms",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")

    @classmethod
    def from_legacy_seconds(
        cls,
        *,
        connect_timeout_seconds: float,
        total_timeout_seconds: float,
        admission_timeout_seconds: float,
        max_scan_bytes: int,
        max_concurrent_scans: int,
        definitions_version: str = "legacy-unverified",
        definitions_fresh_at: datetime | None = None,
    ) -> ClamdLimits:
        seconds = (
            connect_timeout_seconds,
            total_timeout_seconds,
            admission_timeout_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in seconds):
            raise ValueError("clamd timeouts must be finite and positive")
        return cls(
            max_bytes=max_scan_bytes,
            max_concurrent_scans=max_concurrent_scans,
            deadline_ms=max(1, round(total_timeout_seconds * 1000)),
            socket_timeout_ms=max(1, round(connect_timeout_seconds * 1000)),
            admission_timeout_ms=max(1, round(admission_timeout_seconds * 1000)),
            definitions_version=definitions_version,
            definitions_fresh_at=definitions_fresh_at or datetime.now(UTC),
        )
