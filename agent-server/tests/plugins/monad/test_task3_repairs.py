from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from focusproof.domain.plugins.monad.errors import MonadRpcUnavailable
from focusproof.domain.plugins.monad.models import MonadEvidence
from focusproof.domain.plugins.monad.rpc_client import HttpRpcTransport
from focusproof.domain.plugins.monad.verifier import MonadEvidenceVerifier
from tests.plugins.monad.test_verifier import (
    CONTRACT,
    EVENT_TOPIC,
    SELECTOR,
    TX_HASH,
    WALLET,
    rpc_fixture,
)


class Clock:
    value = 100.0

    def __call__(self) -> float:
        return self.value


class BudgetRpc:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.deadlines: list[float] = []

    def chain_id(self, *, deadline: float) -> int:
        self.deadlines.append(deadline)
        self.clock.value += 0.6
        return 1234

    def transaction(self, tx_hash: str, *, deadline: float) -> dict[str, object]:
        self.deadlines.append(deadline)
        self.clock.value += 0.6
        return {"from": WALLET, "to": CONTRACT, "input": SELECTOR}

    def receipt(self, tx_hash: str, *, deadline: float) -> None:
        self.deadlines.append(deadline)
        if self.clock() >= deadline:
            raise MonadRpcUnavailable("deadline_exhausted")

    def code(self, address: str, block_number: int, *, deadline: float) -> bytes:
        raise AssertionError("budget should be exhausted")

    def block_timestamp(self, block_number: int, *, deadline: float) -> int:
        raise AssertionError("budget should be exhausted")


def make_verifier(rpc: object, *, clock: object | None = None) -> MonadEvidenceVerifier:
    return MonadEvidenceVerifier(
        rpc=rpc,
        chain_id=1234,
        contract_address=CONTRACT,
        deployment_block=40,
        increment_selector=SELECTOR,
        incremented_topic=EVENT_TOPIC,
        session_time_tolerance_seconds=60,
        deadline_seconds=1,
        clock=clock,
    )


def test_one_absolute_deadline_is_shared_by_all_rpc_calls() -> None:
    clock = Clock()
    rpc = BudgetRpc(clock)
    result = make_verifier(rpc, clock=clock).verify(
        MonadEvidence(WALLET, TX_HASH, "explanation"),
        datetime.fromtimestamp(1_700_000_000, timezone.utc),
    )
    assert result.status == "unavailable"
    assert result.findings == ("deadline_exhausted",)
    assert len(set(rpc.deadlines)) == 1


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ConnectError("https://secret.example/key"), "transport_unavailable"),
        (httpx.TimeoutException("https://secret.example/key"), "transport_timeout"),
        (
            httpx.HTTPStatusError(
                "secret body",
                request=httpx.Request("POST", "https://secret.example/key"),
                response=httpx.Response(503),
            ),
            "rpc_http_error",
        ),
    ],
)
def test_httpx_failures_are_fixed_safe_codes(monkeypatch, error: Exception, code: str) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(httpx, "stream", fail)
    with pytest.raises(MonadRpcUnavailable) as exc:
        HttpRpcTransport("https://trusted.example").request("eth_chainId", [], 1)
    assert str(exc.value) == code
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("receipt.status", "not-hex"),
        ("code", "0x123"),
        ("topic", "0xzz"),
        ("data", "0xzz"),
        ("block_timestamp", None),
    ],
)
def test_malformed_rpc_data_becomes_retryable_unavailable(path: str, value: object) -> None:
    from tests.plugins.monad.fake_rpc import FakeMonadRpc

    data = rpc_fixture()
    if path == "receipt.status":
        data["receipt"]["status"] = value  # type: ignore[index]
    elif path == "topic":
        data["receipt"]["logs"][0]["topics"][1] = value  # type: ignore[index]
    elif path == "data":
        data["receipt"]["logs"][0]["data"] = value  # type: ignore[index]
    else:
        data[path] = value
    result = make_verifier(FakeMonadRpc(data)).verify(
        MonadEvidence(WALLET, TX_HASH, "explanation"),
        datetime.fromtimestamp(1_700_000_000, timezone.utc),
    )
    assert (result.status, result.findings, result.retryable) == (
        "unavailable",
        ("malformed_response",),
        True,
    )
