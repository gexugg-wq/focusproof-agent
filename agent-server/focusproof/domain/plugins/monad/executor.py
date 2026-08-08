from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Literal, Protocol, cast

from openhands.sdk.tool import Action, Observation, ToolExecutor

from focusproof.domain.plugins.monad.models import MonadEvidence, MonadVerificationStatus
from focusproof.domain.plugins.monad.repository import MonadClaimConflict
from focusproof.domain.plugins.monad.verifier import MonadEvidenceVerifier
from focusproof.runtime.evidence import Evidence


class MonadVerificationAction(Action):
    evidence_id: str


class MonadVerificationObservation(Observation):
    evidence_id: str
    status: Literal["verified", "rejected", "pending", "unavailable"]
    facts: dict[str, int | str]
    findings: list[str]
    block_number: int | None
    retryable: bool


class MonadToolRepository(Protocol):
    def get_monad_verifier(self) -> MonadEvidenceVerifier: ...
    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence: ...
    def claim_monad_transaction(self, **values: Any) -> None: ...


class MonadVerificationExecutor(
    ToolExecutor[MonadVerificationAction, MonadVerificationObservation]
):
    def __init__(self, repository: MonadToolRepository | None, session_id: str) -> None:
        self._repository = repository
        self._session_id = session_id

    def __call__(
        self, action: MonadVerificationAction, conversation: Any | None = None
    ) -> MonadVerificationObservation:
        repository = self._repository
        if repository is None:
            from focusproof.openhands_runtime.tool_registry import get_repository_provider

            repository = cast(MonadToolRepository, get_repository_provider())
        try:
            stored = repository.get_evidence(self._session_id, action.evidence_id)
        except KeyError:
            return _observation(
                action.evidence_id, "unavailable", {}, ("evidence_not_found",), None, False
            )
        try:
            evidence, session_started_at = _load_monad_evidence(stored)
        except (KeyError, TypeError, ValueError):
            return _observation(
                action.evidence_id, "unavailable", {}, ("invalid_evidence_metadata",), None, False
            )
        result = repository.get_monad_verifier().verify(evidence, session_started_at)
        status = result.status
        findings = result.findings
        if status == "verified":
            try:
                repository.claim_monad_transaction(
                    chain_id=result.facts["chain_id"],
                    transaction_hash=result.facts["transaction_hash"],
                    session_id=self._session_id,
                    evidence_id=action.evidence_id,
                    observation_event_id=_source_event_reference(
                        self._session_id, action.evidence_id, conversation
                    ),
                )
            except MonadClaimConflict:
                status = "rejected"
                findings = ("reused_transaction",)
                result = type(result)(status, {}, findings, result.block_number, False)
        return _observation(
            action.evidence_id,
            status,
            result.facts,
            findings,
            result.block_number,
            result.retryable,
        )


def _observation(
    evidence_id: str,
    status: MonadVerificationStatus,
    facts: dict[str, int | str],
    findings: tuple[str, ...],
    block_number: int | None,
    retryable: bool,
) -> MonadVerificationObservation:
    return MonadVerificationObservation.from_text(
        json.dumps(
            {"status": status, "facts": facts, "findings": findings, "retryable": retryable},
            sort_keys=True,
        ),
        evidence_id=evidence_id,
        status=status,
        facts=facts,
        findings=list(findings),
        block_number=block_number,
        retryable=retryable,
    )


def _load_monad_evidence(stored: Evidence) -> tuple[MonadEvidence, datetime]:
    if stored.evidenceType != "monad_transaction":
        raise ValueError
    metadata = stored.metadata
    values = (
        metadata["walletAddress"],
        metadata["transactionHash"],
        metadata["explanation"],
        metadata["sessionStartedAt"],
    )
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError
    wallet, tx_hash, explanation, started = cast(tuple[str, str, str, str], values)
    parsed = datetime.fromisoformat(started)
    if parsed.tzinfo is None:
        raise ValueError
    return MonadEvidence(wallet, tx_hash, explanation), parsed


def _source_event_reference(session_id: str, evidence_id: str, conversation: Any | None) -> str:
    if conversation is not None:
        for event in reversed(tuple(conversation.state.events)):
            if getattr(event, "tool_name", None) == "verify_monad_learning_transaction":
                event_id = getattr(event, "id", None)
                if event_id:
                    return str(event_id)
    digest = sha256(f"{session_id}:{evidence_id}".encode()).hexdigest()
    return f"monad_action_{digest}"
