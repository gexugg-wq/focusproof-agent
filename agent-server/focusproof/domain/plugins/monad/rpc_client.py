from __future__ import annotations

from collections.abc import Mapping
import json
from time import monotonic
from typing import Any, Protocol, cast

import httpx

from focusproof.domain.plugins.monad.errors import MonadRpcUnavailable


_ALLOWED_METHODS = frozenset({"eth_chainId", "eth_getTransactionByHash",
                              "eth_getTransactionReceipt", "eth_getCode", "eth_getBlockByNumber"})


class RpcTransport(Protocol):
    def request(self, method: str, params: list[object], timeout: float) -> bytes: ...


class MonadRpcClient(Protocol):
    def chain_id(self) -> int: ...
    def transaction(self, tx_hash: str) -> dict[str, Any] | None: ...
    def receipt(self, tx_hash: str) -> dict[str, Any] | None: ...
    def code(self, address: str, block_number: int) -> bytes: ...
    def block_timestamp(self, block_number: int) -> int: ...


class HttpRpcTransport:
    def __init__(self, rpc_url: str, *, max_response_bytes: int = 256_000) -> None:
        self._rpc_url = rpc_url
        self._max_response_bytes = max_response_bytes

    def request(self, method: str, params: list[object], timeout: float) -> bytes:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        chunks: list[bytes] = []
        size = 0
        with httpx.stream(
            "POST", self._rpc_url, json=payload, timeout=timeout,
            headers={"accept": "application/json"},
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self._max_response_bytes:
                    raise MonadRpcUnavailable("response_too_large")
                chunks.append(chunk)
        return b"".join(chunks)


class BoundedMonadRpcClient:
    def __init__(self, transport: RpcTransport, *, deadline_seconds: float = 8,
                 max_response_bytes: int = 256_000, max_logs: int = 64) -> None:
        self._transport = transport
        self._deadline_seconds = deadline_seconds
        self._max_response_bytes = max_response_bytes
        self._max_logs = max_logs

    def _request(self, method: str, params: list[object]) -> object:
        if method not in _ALLOWED_METHODS:
            raise ValueError("unsupported Monad RPC method")
        started = monotonic()
        for attempt in range(2):
            remaining = self._deadline_seconds - (monotonic() - started)
            if remaining <= 0:
                raise MonadRpcUnavailable("deadline_exhausted")
            try:
                payload = self._transport.request(method, params, remaining)
            except (TimeoutError, OSError):
                if attempt == 1:
                    raise MonadRpcUnavailable("transport_unavailable") from None
                continue
            if len(payload) > self._max_response_bytes:
                raise MonadRpcUnavailable("response_too_large")
            try:
                message = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise MonadRpcUnavailable("malformed_response") from None
            if not isinstance(message, Mapping) or message.get("error") is not None:
                raise MonadRpcUnavailable("rpc_error")
            return message.get("result")
        raise MonadRpcUnavailable("transport_unavailable")

    def chain_id(self) -> int:
        return int(cast(str, self._request("eth_chainId", [])), 16)

    def transaction(self, tx_hash: str) -> dict[str, Any] | None:
        return cast(dict[str, Any] | None, self._request("eth_getTransactionByHash", [tx_hash]))

    def receipt(self, tx_hash: str) -> dict[str, Any] | None:
        result = cast(dict[str, Any] | None, self._request("eth_getTransactionReceipt", [tx_hash]))
        if result is not None and len(cast(list[object], result.get("logs", []))) > self._max_logs:
            raise MonadRpcUnavailable("too_many_logs")
        return result

    def code(self, address: str, block_number: int) -> bytes:
        value = cast(str, self._request("eth_getCode", [address, hex(block_number)]))
        return bytes.fromhex(value.removeprefix("0x"))

    def block_timestamp(self, block_number: int) -> int:
        block = cast(dict[str, str], self._request("eth_getBlockByNumber", [hex(block_number), False]))
        return int(block["timestamp"], 16)
