from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeMonadRpc:
    responses: dict[str, Any]
    calls: list[str] = field(default_factory=list)

    def chain_id(self) -> int:
        self.calls.append("chain_id")
        return self.responses["chain_id"]

    def transaction(self, tx_hash: str) -> dict[str, Any] | None:
        self.calls.append("transaction")
        return self.responses["transaction"]

    def receipt(self, tx_hash: str) -> dict[str, Any] | None:
        self.calls.append("receipt")
        return self.responses["receipt"]

    def code(self, address: str, block_number: int) -> bytes:
        self.calls.append("code")
        return self.responses["code"]

    def block_timestamp(self, block_number: int) -> int:
        self.calls.append("block_timestamp")
        return self.responses["block_timestamp"]
