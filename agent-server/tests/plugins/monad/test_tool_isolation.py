from __future__ import annotations

from datetime import datetime
from typing import Any

from focusproof.domain.plugins.monad.models import MonadVerificationObservation
from focusproof.domain.plugins.monad.tool import MonadVerificationAction, MonadVerificationTool
from focusproof.runtime.evidence import Evidence


class Verifier:
    def __init__(self, chain_id: int) -> None:
        self.chain_id = chain_id

    def verify(
        self, evidence: object, session_started_at: datetime
    ) -> MonadVerificationObservation:
        return MonadVerificationObservation(
            "verified",
            {
                "chain_id": self.chain_id,
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


class Facade:
    def __init__(self, chain_id: int) -> None:
        self.verifier = Verifier(chain_id)

    def get_monad_verifier(self) -> Verifier:
        return self.verifier

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
        pass


def test_session_facades_isolate_trusted_verifiers_without_global_factory() -> None:
    assert not hasattr(MonadVerificationTool, "verifier_factory")
    first = MonadVerificationTool.create(session_id="sess_1", repository=Facade(111))[0]
    second = MonadVerificationTool.create(session_id="sess_2", repository=Facade(222))[0]
    action = MonadVerificationAction(evidence_id="ev_1")
    assert first.executor(action).facts["chain_id"] == 111
    assert second.executor(action).facts["chain_id"] == 222
