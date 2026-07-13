from __future__ import annotations

from typing import Literal


from focusproof.runtime.actions import Action
from focusproof.runtime.observations import Observation

_KEYWORDS = ("nonce", "gas", "signature", "block", "confirm", "event", "append", "replay", "view")


class FakeTextEvidenceTool:
    name = "FakeTextEvidenceTool"

    def execute(self, action: Action) -> Observation:
        text = str(action.input.get("text") or "")
        keyword_hits = [keyword for keyword in _KEYWORDS if keyword in text.lower()]
        facts = {
            "textLength": len(text),
            "isSpecific": len(text.split()) >= 8 and bool(keyword_hits),
            "keywordHits": keyword_hits,
        }
        return Observation(toolName=self.name, status="success", facts=facts, sourceRefs=action.evidenceIds)


class FakeWeb3TxTool:
    name = "FakeWeb3TxTool"

    def execute(self, action: Action) -> Observation:
        tx_hash = str(action.input.get("hash") or "")
        exists = tx_hash.startswith("0x") and len(tx_hash) >= 10
        status: Literal["success", "failed"] = "success" if exists else "failed"
        facts = {"hash": tx_hash, "exists": exists, "chain": "monad-testnet-mock"}
        error = None if exists else "Transaction hash must start with 0x and be at least 10 characters."
        return Observation(
            toolName=self.name,
            status=status,
            facts=facts,
            sourceRefs=action.evidenceIds,
            error=error,
        )
