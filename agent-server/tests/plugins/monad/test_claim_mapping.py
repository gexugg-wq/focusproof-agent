from __future__ import annotations

from datetime import datetime
from typing import Any

from focusproof.domain.plugins.monad.models import MonadVerificationObservation
from focusproof.domain.plugins.monad.repository import MonadClaimConflict
from focusproof.domain.plugins.monad.tool import MonadVerificationAction, MonadVerificationTool
from focusproof.runtime.evidence import Evidence


class Facade:
    def get_monad_verifier(self) -> "Facade":
        return self

    def verify(
        self, evidence: object, session_started_at: datetime
    ) -> MonadVerificationObservation:
        return MonadVerificationObservation(
            "verified",
            {
                "chain_id": 1234,
                "transaction_hash": "0x" + "ab" * 32,
                "sender": "0xde709f2102306220921060314715629080e2fb77",
                "target": "0x52908400098527886E0F7030069857D2E4169EE7",
                "previous_value": 0,
                "new_value": 1,
            },
            (),
            50,
            False,
        )

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        return Evidence(
            evidenceId=evidence_id,
            evidenceType="monad_transaction",
            contentHash="sha256:public",
            metadata={
                "walletAddress": "0xde709f2102306220921060314715629080e2fb77",
                "transactionHash": "0x" + "ab" * 32,
                "explanation": "increment",
                "sessionStartedAt": "2023-11-14T22:13:20+00:00",
            },
        )

    def claim_monad_transaction(self, **values: Any) -> None:
        raise MonadClaimConflict("reused_transaction")


def test_claim_conflict_maps_to_bounded_reused_transaction_finding() -> None:
    definition = MonadVerificationTool.create(session_id="sess_2", repository=Facade())[0]
    observation = definition.executor(MonadVerificationAction(evidence_id="ev_2"))
    assert observation.status == "rejected"
    assert observation.findings == ["reused_transaction"]
    assert observation.facts == {}
    assert observation.retryable is False
