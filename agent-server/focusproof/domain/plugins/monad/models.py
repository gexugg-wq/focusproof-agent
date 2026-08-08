from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class MonadEvidence:
    wallet_address: str
    transaction_hash: str
    explanation: str


@dataclass(frozen=True, slots=True)
class MonadVerificationObservation:
    status: Literal["verified", "rejected", "pending", "unavailable"]
    facts: dict[str, int | str]
    findings: tuple[str, ...]
    block_number: int | None
    retryable: bool
