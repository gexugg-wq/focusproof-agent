from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from focusproof.persistence.models import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MonadEvidenceClaimModel(Base):
    __tablename__ = "monad_evidence_claims"
    __table_args__ = (
        UniqueConstraint(
            "chain_id",
            "transaction_hash",
            name="uq_monad_claim_chain_transaction",
        ),
        Index(
            "ix_monad_claim_session_evidence",
            "session_id",
            "evidence_id",
        ),
    )

    claim_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("learning_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence.evidence_id", ondelete="CASCADE"),
        nullable=False,
    )
    observation_event_id: Mapped[str] = mapped_column(String(96), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
