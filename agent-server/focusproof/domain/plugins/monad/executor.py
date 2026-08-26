from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Protocol, cast

from openhands.sdk.tool import Action, ToolExecutor

from focusproof.domain.plugins.monad.models import MonadEvidence, MonadVerificationStatus
from focusproof.domain.plugins.monad.repository import MonadClaimConflict
from focusproof.domain.plugins.monad.verifier import MonadEvidenceVerifier
from focusproof.openhands_runtime.tools.verification import VerificationObservation, utc_now
from focusproof.runtime.evidence import Evidence

_CAPABILITY = "monad_learning_transaction"
_VERIFIER_VERSION = "1"
_STATUS_MAP: dict[MonadVerificationStatus, str] = {
    "verified": "success",
    "rejected": "failed",
    "pending": "inconclusive",
    "unavailable": "inconclusive",
}
_SAFE_MESSAGES = {
    "evidence_not_found": "Evidence was not found.",
    "invalid_evidence_metadata": "Evidence metadata is incomplete for Monad verification.",
    "receipt_pending": "The transaction is not finalized yet. You can retry later.",
    "rpc_unavailable": "Monad verification is temporarily unavailable. You can retry later.",
    "deadline_exhausted": "Monad verification timed out before all checks completed. You can retry later.",
    "malformed_response": "The chain provider returned malformed data. You can retry later.",
    "wrong_chain": "The transaction was not found on the configured Monad network.",
    "wrong_sender": "The transaction sender does not match the submitted wallet address.",
    "wrong_contract": "The transaction did not target the configured learning contract.",
    "wrong_selector": "The transaction did not call the required learning method.",
    "missing_event": "The required learning event was not emitted by the transaction.",
    "missing_transition": "The transaction did not prove the required counter increment.",
    "stale_transaction": "The transaction happened outside the allowed session window.",
    "reused_transaction": "This transaction was already claimed by another learning evidence submission.",
}


class MonadVerificationAction(Action):
    evidence_id: str


class MonadToolRepository(Protocol):
    def get_monad_verifier(self) -> MonadEvidenceVerifier: ...
    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence: ...
    def claim_monad_transaction(self, **values: Any) -> None: ...


class MonadVerificationExecutor(
    ToolExecutor[MonadVerificationAction, VerificationObservation]
):
    def __init__(self, repository: MonadToolRepository | None, session_id: str) -> None:
        self._repository = repository
        self._session_id = session_id

    def __call__(
        self, action: MonadVerificationAction, conversation: Any | None = None
    ) -> VerificationObservation:
        started_at = utc_now()
        repository = self._repository
        if repository is None:
            from focusproof.openhands_runtime.tool_registry import get_repository_provider

            repository = cast(MonadToolRepository, get_repository_provider())
        try:
            stored = repository.get_evidence(self._session_id, action.evidence_id)
        except KeyError:
            return _observation(
                action.evidence_id,
                "unavailable",
                {},
                ("evidence_not_found",),
                None,
                False,
                started_at=started_at,
            )
        try:
            evidence, session_started_at = _load_monad_evidence(stored)
        except (KeyError, TypeError, ValueError):
            return _observation(
                action.evidence_id,
                "unavailable",
                {},
                ("invalid_evidence_metadata",),
                None,
                False,
                started_at=started_at,
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
            started_at=started_at,
        )


def _observation(
    evidence_id: str,
    status: MonadVerificationStatus,
    facts: dict[str, int | str],
    findings: tuple[str, ...],
    block_number: int | None,
    retryable: bool,
    *,
    started_at: datetime,
) -> VerificationObservation:
    error_code = findings[0] if findings and status != "verified" else None
    safe_error_message = _SAFE_MESSAGES.get(error_code) if error_code is not None else None
    payload_facts: dict[str, Any] = dict(facts)
    payload_facts["verification_status"] = status
    payload_facts["retryable"] = retryable
    if block_number is not None:
        payload_facts["block_number"] = block_number
    if findings:
        payload_facts["findings"] = list(findings)
    return VerificationObservation.from_text(
        json.dumps(
            {
                "capability": _CAPABILITY,
                "status": _STATUS_MAP[status],
                "facts": payload_facts,
                "weak_signals": list(findings),
                "error_code": error_code,
                "retryable": retryable,
            },
            sort_keys=True,
        ),
        evidence_id=evidence_id,
        capability=_CAPABILITY,
        status=_STATUS_MAP[status],
        facts=payload_facts,
        weak_signals=list(findings),
        source_refs=[evidence_id],
        verifier_version=_VERIFIER_VERSION,
        started_at=started_at,
        completed_at=utc_now(),
        error_code=error_code,
        safe_error_message=safe_error_message,
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