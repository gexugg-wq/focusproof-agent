from __future__ import annotations

from datetime import datetime
from typing import Any

from focusproof.domain.plugins.monad.models import MonadVerificationObservation
from focusproof.domain.plugins.monad.tool import MonadVerificationAction, MonadVerificationTool
from focusproof.runtime.evidence import Evidence


class Verifier:
    def __init__(self, status: str = "verified") -> None:
        self.status = status

    def verify(
        self, evidence: object, session_started_at: datetime
    ) -> MonadVerificationObservation:
        if self.status == "pending":
            return MonadVerificationObservation("pending", {}, ("receipt_pending",), None, True)
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


class Repository:
    def __init__(self, verifier: Verifier | None = None) -> None:
        self.verifier = verifier or Verifier()
        self.loaded: list[tuple[str, str]] = []
        self.claims: list[dict[str, Any]] = []

    def get_monad_verifier(self) -> Verifier:
        return self.verifier

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        self.loaded.append((session_id, evidence_id))
        return Evidence(
            evidenceId=evidence_id,
            evidenceType="monad_transaction",
            contentHash="sha256:public",
            metadata={
                "walletAddress": "0xde709f2102306220921060314715629080e2fb77",
                "transactionHash": "0x" + "ab" * 32,
                "explanation": "I called increment and checked the event.",
                "sessionStartedAt": "2023-11-14T22:13:20+00:00",
            },
        )

    def claim_monad_transaction(self, **values: Any) -> None:
        self.claims.append(values)


def test_action_accepts_only_repository_reference() -> None:
    assert set(MonadVerificationAction.model_fields) == {"evidence_id"}


def test_tool_definition_binds_executor_and_loads_authoritative_evidence() -> None:
    repository = Repository()
    definition = MonadVerificationTool.create(session_id="sess_1", repository=repository)[0]
    observation = definition.executor(MonadVerificationAction(evidence_id="ev_1"))
    assert repository.loaded == [("sess_1", "ev_1")]
    assert repository.claims
    assert observation.status == "success"
    serialized = observation.model_dump_json().lower()
    assert all(secret not in serialized for secret in ("rpc", "receipt", "score"))


def test_pending_observation_is_inconclusive_retryable_and_not_claimed() -> None:
    repository = Repository(Verifier("pending"))
    definition = MonadVerificationTool.create(session_id="sess_1", repository=repository)[0]
    observation = definition.executor(MonadVerificationAction(evidence_id="ev_1"))
    assert observation.status == "inconclusive"
    assert observation.facts["verification_status"] == "pending"
    assert observation.facts["retryable"] is True
    assert observation.error_code == "receipt_pending"
    assert repository.claims == []
