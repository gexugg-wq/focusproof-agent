from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, ClassVar, Self

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)

from focusproof.openhands_runtime.tools import (
    SessionEvidenceRepository,
    read_only_annotations,
)

_GENERIC_PHRASES = ("learned many things", "studied a lot", "learned a lot")
_TX_RE = re.compile(r"^0x[a-fA-F0-9]{8,}$")


class EvidenceVerificationAction(Action):
    evidence_id: str


class EvidenceVerificationObservation(Observation):
    evidence_id: str
    verified: bool
    evidence_type: str
    findings: list[str]
    weak_signals: list[str]
    source_refs: list[str]
    verifier: str


class EvidenceVerificationExecutor(
    ToolExecutor[EvidenceVerificationAction, EvidenceVerificationObservation]
):
    def __init__(
        self,
        repository: SessionEvidenceRepository | None,
        session_id: str,
    ) -> None:
        self._repository = repository
        self._session_id = session_id

    def __call__(
        self,
        action: EvidenceVerificationAction,
        conversation: Any | None = None,
    ) -> EvidenceVerificationObservation:
        del conversation
        repository = self._repository
        if repository is None:
            from focusproof.openhands_runtime.tool_registry import (
                get_repository_provider,
            )

            repository = get_repository_provider()
        evidence = repository.get_evidence(self._session_id, action.evidence_id)
        text = (evidence.textContent or "").strip()
        words = text.split()
        weak_signals: list[str] = []
        findings: list[str] = []

        if evidence.evidenceType == "transaction":
            verified = bool(_TX_RE.fullmatch(text))
            findings.append("Transaction-shaped evidence has a valid hash shape." if verified else "Transaction evidence does not have a valid hash shape.")
            if verified:
                weak_signals.append("A transaction artifact does not establish learner understanding.")
        else:
            verified = bool(text or evidence.sourceUrl)
            findings.append(
                f"Repository evidence contains {len(words)} text words."
                if text
                else "Repository evidence contains a source reference without text."
            )
            lowered = text.lower()
            if len(words) < 9:
                weak_signals.append("Text evidence is short and may lack specificity.")
            if any(phrase in lowered for phrase in _GENERIC_PHRASES):
                weak_signals.append("Text evidence contains generic learning claims.")

        source_refs = [evidence.evidenceId]
        if evidence.sourceUrl:
            source_refs.append(evidence.sourceUrl)
        payload = {
            "evidence_id": evidence.evidenceId,
            "verified": verified,
            "evidence_type": evidence.evidenceType,
            "findings": findings,
            "weak_signals": weak_signals,
            "source_refs": source_refs,
            "verifier": "focusproof-session-repository",
        }
        return EvidenceVerificationObservation.from_text(
            json.dumps(payload, sort_keys=True),
            evidence_id=evidence.evidenceId,
            verified=verified,
            evidence_type=evidence.evidenceType,
            findings=findings,
            weak_signals=weak_signals,
            source_refs=source_refs,
            verifier="focusproof-session-repository",
        )


class FocusProofEvidenceVerificationTool(
    ToolDefinition[EvidenceVerificationAction, EvidenceVerificationObservation]
):
    name: ClassVar[str] = "focusproof_evidence_verification"

    @classmethod
    def annotations_for_focusproof(cls) -> ToolAnnotations:
        return read_only_annotations("FocusProof evidence verification")

    @classmethod
    def create(
        cls,
        conv_state: Any | None = None,
        *,
        session_id: str,
        repository: SessionEvidenceRepository | None = None,
    ) -> Sequence[Self]:
        del conv_state
        return [
            cls(
                description=(
                    "Verify one submitted evidence item by repository evidence_id. "
                    "Never accept evidence text from tool arguments."
                ),
                action_type=EvidenceVerificationAction,
                observation_type=EvidenceVerificationObservation,
                executor=EvidenceVerificationExecutor(repository, session_id),
                annotations=cls.annotations_for_focusproof(),
            )
        ]
