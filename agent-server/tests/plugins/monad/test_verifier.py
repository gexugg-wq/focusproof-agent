from __future__ import annotations

from datetime import datetime, timezone

import pytest

from focusproof.domain.plugins.monad.models import MonadEvidence
from focusproof.domain.plugins.monad.verifier import MonadEvidenceVerifier
from tests.plugins.monad.fake_rpc import FakeMonadRpc


CONTRACT = "0x52908400098527886E0F7030069857D2E4169EE7"
WALLET = "0xde709f2102306220921060314715629080e2fb77"
TX_HASH = "0x" + "ab" * 32
SELECTOR = "0xd09de08a"
EVENT_TOPIC = "0x" + "11" * 32


def rpc_fixture() -> dict[str, object]:
    return {
        "chain_id": 1234,
        "transaction": {"from": WALLET, "to": CONTRACT, "input": SELECTOR},
        "receipt": {
            "status": 1,
            "blockNumber": 50,
            "to": CONTRACT,
            "logs": [{
                "address": CONTRACT,
                "topics": [EVENT_TOPIC, "0x" + "00" * 12 + WALLET[2:]],
                "data": "0x" + (0).to_bytes(32, "big").hex() + (1).to_bytes(32, "big").hex(),
            }],
        },
        "code": b"\x60\x00",
        "block_timestamp": 1_700_000_010,
    }


def make_verifier(responses: dict[str, object] | None = None) -> MonadEvidenceVerifier:
    return MonadEvidenceVerifier(
        rpc=FakeMonadRpc(responses or rpc_fixture()), chain_id=1234,
        contract_address=CONTRACT, deployment_block=40,
        increment_selector=SELECTOR, incremented_topic=EVENT_TOPIC,
        session_time_tolerance_seconds=60,
    )


def evidence(wallet: str = WALLET) -> MonadEvidence:
    return MonadEvidence(wallet_address=wallet, transaction_hash=TX_HASH, explanation="I incremented")


def test_verifies_bounded_facts_and_normalizes_lowercase_wallet() -> None:
    result = make_verifier().verify(evidence(), datetime.fromtimestamp(1_700_000_000, timezone.utc))
    assert result.status == "verified"
    assert result.retryable is False
    assert result.findings == ()
    assert result.facts == {"chain_id": 1234, "transaction_hash": TX_HASH, "sender": WALLET,
                            "target": CONTRACT, "previous_value": 0, "new_value": 1}
    assert result.block_number == 50


@pytest.mark.parametrize(
    ("path", "value", "finding", "status", "retryable"),
    [
        ("chain_id", 999, "wrong_chain", "rejected", False),
        ("transaction", None, "transaction_unavailable", "unavailable", True),
        ("receipt", None, "receipt_pending", "pending", True),
        ("receipt.status", 0, "transaction_failed", "rejected", False),
        ("transaction.from", "0x" + "12" * 20, "wrong_sender", "rejected", False),
        ("transaction.to", "0x" + "12" * 20, "wrong_contract", "rejected", False),
        ("code", b"", "missing_contract_code", "rejected", False),
        ("transaction.input", "0x12345678", "wrong_selector", "rejected", False),
        ("receipt.logs", [], "missing_increment_event", "rejected", False),
        ("learner", "0x" + "12" * 20, "event_learner_mismatch", "rejected", False),
        ("transition", (3, 5), "invalid_increment_transition", "rejected", False),
        ("block_timestamp", 1_699_999_000, "stale_transaction", "rejected", False),
    ],
)
def test_negative_matrix(path: str, value: object, finding: str, status: str, retryable: bool) -> None:
    data = rpc_fixture()
    if path.startswith("transaction."):
        data["transaction"][path.split(".")[1]] = value  # type: ignore[index]
    elif path == "receipt.status":
        data["receipt"]["status"] = value  # type: ignore[index]
    elif path == "receipt.logs":
        data["receipt"]["logs"] = value  # type: ignore[index]
    elif path == "learner":
        data["receipt"]["logs"][0]["topics"][1] = "0x" + "00" * 12 + str(value)[2:]  # type: ignore[index]
    elif path == "transition":
        previous, new = value  # type: ignore[misc]
        data["receipt"]["logs"][0]["data"] = "0x" + previous.to_bytes(32, "big").hex() + new.to_bytes(32, "big").hex()  # type: ignore[index]
    else:
        data[path] = value
    result = make_verifier(data).verify(evidence(), datetime.fromtimestamp(1_700_000_000, timezone.utc))
    assert (result.status, result.findings, result.retryable) == (status, (finding,), retryable)


def test_rejects_bad_mixed_case_wallet_checksum() -> None:
    result = make_verifier().verify(evidence("0x52908400098527886E0F7030069857D2E4169Ee7"),
                                    datetime.fromtimestamp(1_700_000_000, timezone.utc))
    assert result.findings == ("invalid_wallet_address",)
