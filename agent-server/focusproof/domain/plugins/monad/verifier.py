from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import monotonic
from typing import Any

from focusproof.domain.plugins.monad.errors import MonadRpcUnavailable
from focusproof.domain.plugins.monad.models import (
    MonadEvidence,
    MonadVerificationObservation,
    MonadVerificationStatus,
)
from focusproof.domain.plugins.monad.rpc_client import MonadRpcClient


class MonadEvidenceVerifier:
    def __init__(
        self,
        *,
        rpc: MonadRpcClient,
        chain_id: int,
        contract_address: str,
        deployment_block: int,
        increment_selector: str,
        incremented_topic: str,
        session_time_tolerance_seconds: int = 300,
        deadline_seconds: float = 8,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._rpc = rpc
        self._chain_id = chain_id
        self._contract = contract_address
        self._deployment_block = deployment_block
        self._selector = increment_selector.lower()
        self._topic = incremented_topic.lower()
        self._tolerance = session_time_tolerance_seconds
        self._deadline_seconds = deadline_seconds
        self._clock = clock or monotonic

    def verify(
        self, evidence: MonadEvidence, session_started_at: datetime
    ) -> MonadVerificationObservation:
        deadline = self._clock() + self._deadline_seconds
        wallet = _normalize_address(evidence.wallet_address)
        if wallet is None:
            return _result("rejected", "invalid_wallet_address")
        try:
            if self._rpc.chain_id(deadline=deadline) != self._chain_id:
                return _result("rejected", "wrong_chain")
            transaction = self._rpc.transaction(evidence.transaction_hash, deadline=deadline)
            if transaction is None:
                return _result("unavailable", "transaction_unavailable", retryable=True)
            receipt = self._rpc.receipt(evidence.transaction_hash, deadline=deadline)
            if receipt is None:
                return _result("pending", "receipt_pending", retryable=True)
            if _quantity(receipt.get("status")) != 1:
                return _result("rejected", "transaction_failed")
            sender = _normalize_address(str(transaction.get("from", "")))
            if sender != wallet:
                return _result("rejected", "wrong_sender")
            target = _normalize_address(str(transaction.get("to", "")))
            if target != self._contract:
                return _result("rejected", "wrong_contract")
            block_number = _quantity(receipt.get("blockNumber"))
            code = self._rpc.code(self._contract, block_number, deadline=deadline)
            if not isinstance(code, bytes):
                raise ValueError
            if block_number < self._deployment_block or not code:
                return _result("rejected", "missing_contract_code", block_number=block_number)
            if str(transaction.get("input", "")).lower() != self._selector:
                return _result("rejected", "wrong_selector", block_number=block_number)
            event = self._find_event(receipt.get("logs", []))
            if event is None:
                return _result("rejected", "missing_increment_event", block_number=block_number)
            learner, previous, new = event
            if learner != wallet:
                return _result("rejected", "event_learner_mismatch", block_number=block_number)
            if new != previous + 1:
                return _result(
                    "rejected", "invalid_increment_transition", block_number=block_number
                )
            timestamp = self._rpc.block_timestamp(block_number, deadline=deadline)
            if not isinstance(timestamp, int):
                raise ValueError
            if timestamp < int(session_started_at.timestamp()) - self._tolerance:
                return _result("rejected", "stale_transaction", block_number=block_number)
        except MonadRpcUnavailable as exc:
            finding = "deadline_exhausted" if exc.code == "deadline_exhausted" else "rpc_unavailable"
            return _result("unavailable", finding, retryable=True)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError):
            return _result("unavailable", "malformed_response", retryable=True)
        facts: dict[str, int | str] = {
            "chain_id": self._chain_id,
            "transaction_hash": evidence.transaction_hash.lower(),
            "sender": wallet,
            "target": self._contract,
            "previous_value": previous,
            "new_value": new,
        }
        return MonadVerificationObservation("verified", facts, (), block_number, False)

    def _find_event(self, logs: object) -> tuple[str, int, int] | None:
        if not isinstance(logs, list):
            raise ValueError
        for raw in logs[:64]:
            if not isinstance(raw, dict):
                raise ValueError
            if _normalize_address(str(raw.get("address", ""))) != self._contract:
                continue
            topics = raw.get("topics", [])
            if not isinstance(topics, list) or len(topics) != 2:
                raise ValueError
            if str(topics[0]).lower() != self._topic:
                continue
            learner_topic = str(topics[1])
            if len(learner_topic) != 66:
                raise ValueError
            bytes.fromhex(learner_topic[2:])
            learner = _normalize_address("0x" + learner_topic[-40:])
            data = str(raw.get("data", "")).removeprefix("0x")
            if learner is None or len(data) != 128:
                raise ValueError
            return learner, int(data[:64], 16), int(data[64:], 16)
        return None


def _normalize_address(value: str) -> str | None:
    try:
        from web3 import Web3

        if (
            value != value.lower()
            and value != value.upper()
            and not Web3.is_checksum_address(value)
        ):
            return None
        return str(Web3.to_checksum_address(value))
    except (ImportError, ValueError):
        return None


def _quantity(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError
    return int(value, 16) if isinstance(value, str) else int(value)


def _result(
    status: MonadVerificationStatus,
    finding: str,
    *,
    retryable: bool = False,
    block_number: int | None = None,
) -> MonadVerificationObservation:
    return MonadVerificationObservation(status, {}, (finding,), block_number, retryable)
