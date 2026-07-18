from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import hmac
from typing import Literal, Protocol, runtime_checkable
from uuid import uuid4

MIN_SECURITY_AUDIT_HMAC_KEY_BYTES = 32
MIN_SECURITY_AUDIT_RETENTION_SECONDS = 60
MAX_SECURITY_AUDIT_RETENTION_SECONDS = 90 * 24 * 60 * 60
DEFAULT_SECURITY_AUDIT_RETENTION_SECONDS = 30 * 24 * 60 * 60

SecurityAuditOutcome = Literal["success", "failure"]
SecurityAuditReasonCategory = Literal[
    "success",
    "missing_credentials",
    "invalid_credentials",
    "forbidden",
    "not_found",
    "dependency_unavailable",
    "internal_failure",
]

SECURITY_AUDIT_OUTCOMES = frozenset(("success", "failure"))
SECURITY_AUDIT_REASON_CATEGORIES = frozenset(
    (
        "success",
        "missing_credentials",
        "invalid_credentials",
        "forbidden",
        "not_found",
        "dependency_unavailable",
        "internal_failure",
    )
)


@runtime_checkable
class SecurityAuditSink(Protocol):
    def record(
        self,
        *,
        principal_id: str | None,
        request_id: str,
        token_fingerprint: str | None,
        outcome: SecurityAuditOutcome,
        reason_category: SecurityAuditReasonCategory,
        occurred_at: datetime,
    ) -> None: ...

    def sweep_expired(self, *, now: datetime) -> int: ...


@dataclass(slots=True)
class RequestSecurityAuditContext:
    request_id: str = field(default_factory=lambda: f"req_{uuid4().hex}")
    principal_id: str | None = None
    token_fingerprint: str | None = None
    recorded: bool = False


def compute_token_fingerprint(token: bytes, key: str) -> str:
    return hmac.new(key.encode("utf-8"), token, hashlib.sha256).hexdigest()


def validate_security_audit_hmac_key(value: str) -> str:
    if not value or value != value.strip():
        raise ValueError("security audit HMAC key must be exact and non-empty")
    if len(value.encode("utf-8")) < MIN_SECURITY_AUDIT_HMAC_KEY_BYTES:
        raise ValueError("security audit HMAC key is too weak")
    return value


def validate_security_audit_retention_seconds(value: int) -> int:
    if value < MIN_SECURITY_AUDIT_RETENTION_SECONDS:
        raise ValueError("security audit retention interval is too short")
    if value > MAX_SECURITY_AUDIT_RETENTION_SECONDS:
        raise ValueError("security audit retention interval is too long")
    return value


def raw_bearer_token_bytes(authorization: str | None) -> bytes | None:
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if scheme != "Bearer" or not separator or not token.strip():
        return None
    return token.encode("utf-8")


def invalid_authorization_reason(
    authorization: str | None,
) -> SecurityAuditReasonCategory:
    if raw_bearer_token_bytes(authorization) is None:
        return "missing_credentials"
    return "invalid_credentials"
