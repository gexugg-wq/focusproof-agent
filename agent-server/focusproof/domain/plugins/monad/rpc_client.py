from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from time import monotonic
from typing import Any, Protocol, cast

import httpx

from focusproof.domain.plugins.monad.errors import MonadRpcUnavailable


_ALLOWED_METHODS = frozenset(
    {
        "eth_chainId",
        "eth_getTransactionByHash",
        "eth_getTransactionReceipt",
        "eth_getCode",
        "eth_getBlockByNumber",
    }
)


class RpcTransport(Protocol):
    def request(self, method: str, params: list[object], timeout: float) -> bytes: ...


class MonadRpcClient(Protocol):
    def chain_id(self, *, deadline: float) -> int: ...
    def transaction(self, tx_hash: str, *, deadline: float) -> dict[str, Any] | None: ...
    def receipt(self, tx_hash: str, *, deadline: float) -> dict[str, Any] | None: ...
    def code(self, address: str, block_number: int, *, deadline: float) -> bytes: ...
    def block_timestamp(self, block_number: int, *, deadline: float) -> int: ...


class HttpRpcTransport:
    def __init__(self, rpc_url: str, *, max_response_bytes: int = 256_000) -> None:
        self._rpc_url = rpc_url
        self._max_response_bytes = max_response_bytes

    def request(self, method: str, params: list[object], timeout: float) -> bytes:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        chunks: list[bytes] = []
        size = 0
        try:
            with httpx.stream(
                "POST",
                self._rpc_url,
                json=payload,
                timeout=timeout,
                headers={"accept": "application/json"},
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self._max_response_bytes:
                        raise MonadRpcUnavailable("response_too_large")
                    chunks.append(chunk)
        except httpx.TimeoutException:
            raise MonadRpcUnavailable("transport_timeout") from None
        except httpx.HTTPStatusError:
            raise MonadRpcUnavailable("rpc_http_error") from None
        except httpx.TransportError:
            raise MonadRpcUnavailable("transport_unavailable") from None
        return b"".join(chunks)


class BoundedMonadRpcClient:
    def __init__(
        self,
        transport: RpcTransport,
        *,
        max_response_bytes: int = 256_000,
        max_logs: int = 64,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._transport = transport
        self._max_response_bytes = max_response_bytes
        self._max_logs = max_logs
        self._clock = clock

    def _request(self, method: str, params: list[object], *, deadline: float) -> object:
        if method not in _ALLOWED_METHODS:
            raise ValueError("unsupported Monad RPC method")
        for attempt in range(2):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise MonadRpcUnavailable("deadline_exhausted")
            try:
                payload = self._transport.request(method, params, remaining)
            except MonadRpcUnavailable as exc:
                if attempt == 1 or str(exc) not in {"transport_timeout", "transport_unavailable"}:
                    raise
                continue
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

    def chain_id(self, *, deadline: float) -> int:
        return _hex_quantity(self._request("eth_chainId", [], deadline=deadline))

    def transaction(self, tx_hash: str, *, deadline: float) -> dict[str, Any] | None:
        value = self._request("eth_getTransactionByHash", [tx_hash], deadline=deadline)
        if value is not None and not isinstance(value, dict):
            raise MonadRpcUnavailable("malformed_response")
        return cast(dict[str, Any] | None, value)

    def receipt(self, tx_hash: str, *, deadline: float) -> dict[str, Any] | None:
        value = self._request("eth_getTransactionReceipt", [tx_hash], deadline=deadline)
        if value is None:
            return None
        if not isinstance(value, dict) or not isinstance(value.get("logs", []), list):
            raise MonadRpcUnavailable("malformed_response")
        if len(value.get("logs", [])) > self._max_logs:
            raise MonadRpcUnavailable("too_many_logs")
        return cast(dict[str, Any], value)

    def code(self, address: str, block_number: int, *, deadline: float) -> bytes:
        value = self._request("eth_getCode", [address, hex(block_number)], deadline=deadline)
        if not isinstance(value, str) or not value.startswith("0x") or len(value[2:]) % 2:
            raise MonadRpcUnavailable("malformed_response")
        try:
            return bytes.fromhex(value[2:])
        except ValueError:
            raise MonadRpcUnavailable("malformed_response") from None

    def block_timestamp(self, block_number: int, *, deadline: float) -> int:
        block = self._request("eth_getBlockByNumber", [hex(block_number), False], deadline=deadline)
        if not isinstance(block, dict) or "timestamp" not in block:
            raise MonadRpcUnavailable("malformed_response")
        return _hex_quantity(block["timestamp"])


def _hex_quantity(value: object) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise MonadRpcUnavailable("malformed_response")
    try:
        return int(value, 16)
    except ValueError:
        raise MonadRpcUnavailable("malformed_response") from None
