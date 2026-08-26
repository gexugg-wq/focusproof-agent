from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from focusproof.domain.plugins.monad.claim_model import MonadEvidenceClaimModel


class MonadClaimConflict(RuntimeError):
    """The transaction is already owned by a different evidence record."""


@dataclass(frozen=True, slots=True)
class MonadClaimResult:
    claim_id: str
    transaction_hash: str
    created: bool


class MonadClaimRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim(
        self,
        chain_id: int,
        tx_hash: str,
        session_id: str,
        evidence_id: str,
        observation_event_id: str,
    ) -> MonadClaimResult:
        normalized = _normalize_transaction_hash(tx_hash)
        existing = self._find(chain_id, normalized)
        if existing is not None:
            return self._resolve(existing, session_id, evidence_id)
        model = MonadEvidenceClaimModel(
            claim_id=f"mclaim_{uuid4().hex}",
            chain_id=chain_id,
            transaction_hash=normalized,
            session_id=session_id,
            evidence_id=evidence_id,
            observation_event_id=observation_event_id,
        )
        _ensure_outer_sqlite_transaction(self._session.connection())
        try:
            with self._session.begin_nested():
                self._session.add(model)
                self._session.flush()
        except IntegrityError:
            winner = self._find(chain_id, normalized)
            if winner is None:
                raise
            return self._resolve(winner, session_id, evidence_id)
        return MonadClaimResult(model.claim_id, normalized, True)

    def _find(self, chain_id: int, tx_hash: str) -> MonadEvidenceClaimModel | None:
        return self._session.scalar(
            select(MonadEvidenceClaimModel).where(
                MonadEvidenceClaimModel.chain_id == chain_id,
                MonadEvidenceClaimModel.transaction_hash == tx_hash,
            )
        )

    @staticmethod
    def _resolve(
        model: MonadEvidenceClaimModel, session_id: str, evidence_id: str
    ) -> MonadClaimResult:
        if model.session_id != session_id or model.evidence_id != evidence_id:
            raise MonadClaimConflict("reused_transaction")
        return MonadClaimResult(model.claim_id, model.transaction_hash, False)


def _normalize_transaction_hash(value: str) -> str:
    normalized = value.lower()
    if not normalized.startswith("0x") or len(normalized) != 66:
        raise ValueError("invalid transaction hash")
    try:
        bytes.fromhex(normalized[2:])
    except ValueError:
        raise ValueError("invalid transaction hash") from None
    return normalized


def _ensure_outer_sqlite_transaction(connection: Connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    driver = cast(sqlite3.Connection, connection.connection.driver_connection)
    if not driver.in_transaction:
        connection.exec_driver_sql("BEGIN")
