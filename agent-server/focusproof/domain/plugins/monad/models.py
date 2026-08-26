from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MonadVerificationStatus = Literal["verified", "rejected", "pending", "unavailable"]


@dataclass(frozen=True, slots=True)
class MonadEvidence:
    wallet_address: str
    transaction_hash: str
    explanation: str


@dataclass(frozen=True, slots=True)
class MonadVerificationObservation:
    status: MonadVerificationStatus
    facts: dict[str, int | str]
    findings: tuple[str, ...]
    block_number: int | None
    retryable: bool
