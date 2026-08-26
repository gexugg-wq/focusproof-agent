from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType


class ScanResultKind(StrEnum):
    CLEAN = "clean"
    MALICIOUS = "malicious"
    OVERSIZE = "oversize"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ScanRejectionCode(StrEnum):
    MALWARE_SIGNATURE_DETECTED = "malware_signature_detected"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    DAEMON_UNAVAILABLE = "daemon_unavailable"
    DAEMON_ERROR = "daemon_error"
    LEGACY_UNKNOWN_UNCLASSIFIED = "legacy_unknown_unclassified"


SCAN_RESULT_REJECTION_CODES: Mapping[
    ScanResultKind, tuple[ScanRejectionCode | None, ...]
] = MappingProxyType(
    {
        ScanResultKind.CLEAN: (None,),
        ScanResultKind.MALICIOUS: (ScanRejectionCode.MALWARE_SIGNATURE_DETECTED,),
        ScanResultKind.OVERSIZE: (ScanRejectionCode.PAYLOAD_TOO_LARGE,),
        ScanResultKind.TIMEOUT: (ScanRejectionCode.DEADLINE_EXCEEDED,),
        ScanResultKind.UNAVAILABLE: (ScanRejectionCode.DAEMON_UNAVAILABLE,),
        ScanResultKind.ERROR: (
            ScanRejectionCode.DAEMON_ERROR,
            ScanRejectionCode.LEGACY_UNKNOWN_UNCLASSIFIED,
        ),
    }
)

DEFAULT_SCAN_REJECTION_CODES: Mapping[ScanResultKind, ScanRejectionCode] = MappingProxyType(
    {
        ScanResultKind.MALICIOUS: ScanRejectionCode.MALWARE_SIGNATURE_DETECTED,
        ScanResultKind.OVERSIZE: ScanRejectionCode.PAYLOAD_TOO_LARGE,
        ScanResultKind.TIMEOUT: ScanRejectionCode.DEADLINE_EXCEEDED,
        ScanResultKind.UNAVAILABLE: ScanRejectionCode.DAEMON_UNAVAILABLE,
        ScanResultKind.ERROR: ScanRejectionCode.DAEMON_ERROR,
    }
)


def default_scan_rejection_code(result: ScanResultKind) -> ScanRejectionCode:
    try:
        return DEFAULT_SCAN_REJECTION_CODES[result]
    except KeyError as exc:
        raise ValueError(f"{result.value} scan result has no default rejection code") from exc


def _result_rejection_code_check_sql() -> str:
    clauses: list[str] = []
    for result, codes in SCAN_RESULT_REJECTION_CODES.items():
        concrete_codes = tuple(code for code in codes if code is not None)
        if not concrete_codes:
            predicate = "rejection_code IS NULL"
        elif len(concrete_codes) == 1:
            predicate = f"rejection_code = '{concrete_codes[0].value}'"
        else:
            values = ", ".join(f"'{code.value}'" for code in concrete_codes)
            predicate = f"rejection_code IN ({values})"
        clauses.append(f"(scan_result = '{result.value}' AND {predicate})")
    return f"({' OR '.join(clauses)})"


SCAN_RESULT_REJECTION_CODE_CHECK_SQL = _result_rejection_code_check_sql()


@dataclass(frozen=True, slots=True)
class MediaScanAuditSnapshot:
    scanner_backend: str
    definitions_version: str
    definitions_fresh_at: datetime
    definitions_age_seconds: int
    max_bytes: int
    max_concurrent_scans: int
    deadline_ms: int
    socket_timeout_ms: int

    def __post_init__(self) -> None:
        if not self.scanner_backend.strip():
            raise ValueError("scanner_backend must not be blank")
        if not self.definitions_version.strip():
            raise ValueError("definitions_version must not be blank")
        if self.definitions_fresh_at.tzinfo is None:
            raise ValueError("definitions_fresh_at must be timezone-aware")
        if self.definitions_age_seconds < 0:
            raise ValueError("definitions_age_seconds must be non-negative")
        for field_name in (
            "max_bytes",
            "max_concurrent_scans",
            "deadline_ms",
            "socket_timeout_ms",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")
