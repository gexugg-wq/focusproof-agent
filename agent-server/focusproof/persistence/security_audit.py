from __future__ import annotations

from datetime import datetime, timedelta

from focusproof.persistence.repositories import StoredSecurityAuditEvent
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike
from focusproof.runtime.security_audit import (
    SecurityAuditOutcome,
    SecurityAuditReasonCategory,
)

SECURITY_AUDIT_RETENTION_BATCH_SIZE = 64


class PersistentSecurityAuditSink:
    """Durable product security audit; not an OpenHands runtime fact source."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactoryLike,
        *,
        retention_seconds: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._retention = timedelta(seconds=retention_seconds)

    def record(
        self,
        *,
        principal_id: str | None,
        request_id: str,
        token_fingerprint: str | None,
        outcome: SecurityAuditOutcome,
        reason_category: SecurityAuditReasonCategory,
        occurred_at: datetime,
    ) -> None:
        cutoff = occurred_at - self._retention
        with self._uow_factory() as uow:
            uow.security_audit.add(
                StoredSecurityAuditEvent(
                    request_id=request_id,
                    principal_id=principal_id,
                    token_fingerprint=token_fingerprint,
                    outcome=outcome,
                    reason_category=reason_category,
                    occurred_at=occurred_at,
                )
            )
            uow.security_audit.delete_expired(
                cutoff=cutoff,
                limit=SECURITY_AUDIT_RETENTION_BATCH_SIZE,
            )
            uow.commit()

    def sweep_expired(self, *, now: datetime) -> int:
        cutoff = now - self._retention
        with self._uow_factory() as uow:
            deleted = uow.security_audit.delete_expired(
                cutoff=cutoff,
                limit=SECURITY_AUDIT_RETENTION_BATCH_SIZE,
            )
            uow.commit()
            return deleted
