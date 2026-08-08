from __future__ import annotations

import json

import pytest

from focusproof.domain.plugins.monad.errors import MonadRpcUnavailable
from focusproof.domain.plugins.monad.rpc_client import BoundedMonadRpcClient


class Transport:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls = 0

    def request(self, method: str, params: list[object], timeout: float) -> bytes:
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()


def test_allows_only_fixed_rpc_methods() -> None:
    client = BoundedMonadRpcClient(Transport([]), deadline_seconds=1, max_response_bytes=1000)
    with pytest.raises(ValueError, match="RPC method"):
        client._request("eth_sendRawTransaction", [])


def test_rejects_oversized_response_without_exposing_payload() -> None:
    client = BoundedMonadRpcClient(Transport(["x" * 100]), deadline_seconds=1, max_response_bytes=32)
    with pytest.raises(MonadRpcUnavailable, match="response_too_large") as exc:
        client.chain_id()
    assert "xxx" not in str(exc.value)


def test_retries_transport_failure_once() -> None:
    transport = Transport([TimeoutError("secret endpoint"), "0x4d2"])
    client = BoundedMonadRpcClient(transport, deadline_seconds=1, max_response_bytes=1000)
    assert client.chain_id() == 1234
    assert transport.calls == 2


def test_deadline_exhaustion_is_sanitized() -> None:
    transport = Transport([TimeoutError("https://secret-rpc.example/key") for _ in range(2)])
    client = BoundedMonadRpcClient(transport, deadline_seconds=0, max_response_bytes=1000)
    with pytest.raises(MonadRpcUnavailable, match="deadline_exhausted") as exc:
        client.chain_id()
    assert "secret-rpc" not in str(exc.value)
