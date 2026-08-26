from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from focusproof.media_adapters.local_media_object_store import LocalMediaObjectStore
from focusproof.media_adapters.local_quarantine_store import LocalQuarantineStore


@dataclass(frozen=True, slots=True)
class JanitorDiagnostic:
    artifact_id: str
    target: str
    operation: str
    error_type: str
    retryable: bool


class MediaJanitor:
    """Coordinates store-owned recovery and never accepts filesystem paths."""

    def __init__(self, *, quarantine_store: LocalQuarantineStore,
                 object_store: LocalMediaObjectStore,
                 reference_checker: Callable[[str], bool | None],
                 reservation_active_checker: Callable[[str], bool | None],
                 uow_factory: Callable[[], Any] | None = None,
                 pending_batch_size: int = 100) -> None:
        self._quarantine_store = quarantine_store
        self._object_store = object_store
        self._reference_checker = reference_checker
        self._reservation_active_checker = reservation_active_checker
        self._uow_factory = uow_factory
        self._pending_batch_size = pending_batch_size
        self.last_diagnostics: tuple[object, ...] = ()

    def sweep(self, *, older_than_seconds: float) -> tuple[tuple[str, ...], tuple[str, ...]]:
        recovered = self._quarantine_store.recover_quarantine(
            self._reservation_active_checker, older_than_seconds=older_than_seconds
        )
        expired = self._quarantine_store.remove_expired(now=datetime.now(UTC))
        staged = self._object_store.recover_staged(
            self._reference_checker, older_than_seconds=older_than_seconds
        )
        diagnostics: list[object] = []
        diagnostics.extend(self._bounded_diagnostics(self._quarantine_store.last_diagnostics))
        diagnostics.extend(
            self._bounded_diagnostics(getattr(self._object_store, "last_diagnostics", ()))
        )
        diagnostics.extend(self._recover_pending_clean_receipts())
        self.last_diagnostics = tuple(diagnostics[:128])
        return expired + recovered, staged

    @staticmethod
    def _bounded_diagnostics(value: Any) -> tuple[object, ...]:
        if not isinstance(value, tuple):
            return ()
        return value[:128]

    def _recover_pending_clean_receipts(self) -> tuple[object, ...]:
        if self._uow_factory is None:
            return ()
        diagnostics: list[object] = []
        try:
            with self._uow_factory() as uow:
                pending = uow.scan_audit.list_expired_pending_clean_receipts(
                    now=datetime.now(UTC),
                    limit=self._pending_batch_size,
                )
                uow.commit()
        except Exception as exc:
            return (
                JanitorDiagnostic(
                    artifact_id="pending",
                    target="pending",
                    operation="list",
                    error_type=type(exc).__name__,
                    retryable=True,
                ),
            )
        for item in pending:
            if len(diagnostics) >= 128:
                break
            cleanup_ok = self._quarantine_store.discard_pending_clean_receipt(item)
            diagnostics.extend(self._bounded_diagnostics(self._quarantine_store.last_diagnostics))
            if not cleanup_ok:
                continue
            try:
                with self._uow_factory() as uow:
                    uow.scan_audit.delete_pending_clean_receipt(item.receipt_id)
                    uow.commit()
            except Exception as exc:
                diagnostics.append(
                    JanitorDiagnostic(
                        artifact_id=item.receipt_id,
                        target="pending",
                        operation="delete",
                        error_type=type(exc).__name__,
                        retryable=True,
                    )
                )
        return tuple(diagnostics[:128])
