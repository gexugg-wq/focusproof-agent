from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, ClassVar, Self

from openhands.sdk.tool import ToolAnnotations, ToolDefinition, ToolExecutor

from focusproof.openhands_runtime.tools import (
    SessionEvidenceRepository,
    read_only_annotations,
)
from focusproof.openhands_runtime.tools.verification import (
    EvidenceReferenceAction,
    VerificationObservation,
    utc_now,
)

_GENERIC_LEARNING_CLAIMS = (
    "i learned a lot",
    "learned many things",
    "studied a lot",
    "learned a lot",
)
_EXAMPLE_MARKERS = (
    "for example",
    "concrete example",
    "example",
    "e.g.",
    "compared",
)
_STRUCTURE_RE = re.compile(r"(?m)^(?:#{1,6}\s+|\d+[.)]\s+|[-*]\s+)|```")
_CJK_CHARACTER_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)
_NON_CJK_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)
_MIN_SPECIFIC_WORDS = 9
_VERIFIER_VERSION = "1"


def _lexical_unit_count(text: str) -> int:
    cjk_count = len(_CJK_CHARACTER_RE.findall(text))
    if cjk_count == 0:
        return len(text.split())
    non_cjk_text = _CJK_CHARACTER_RE.sub(" ", text)
    return cjk_count + len(_NON_CJK_WORD_RE.findall(non_cjk_text))


class TextEvidenceVerificationExecutor(
    ToolExecutor[EvidenceReferenceAction, VerificationObservation]
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
        action: EvidenceReferenceAction,
        conversation: Any | None = None,
    ) -> VerificationObservation:
        del conversation
        started_at = utc_now()
        repository = self._repository
        if repository is None:
            from focusproof.openhands_runtime.tool_registry import (
                get_repository_provider,
            )

            repository = get_repository_provider()
        try:
            evidence = repository.get_evidence(self._session_id, action.evidence_id)
        except KeyError:
            return VerificationObservation.from_text(
                "Evidence was not found.",
                evidence_id=action.evidence_id,
                capability="text",
                status="failed",
                facts={},
                weak_signals=[],
                source_refs=[action.evidence_id],
                verifier_version=_VERIFIER_VERSION,
                started_at=started_at,
                completed_at=utc_now(),
                error_code="evidence_not_found",
                safe_error_message="Evidence was not found.",
            )

        source_refs = [evidence.evidenceId, evidence.contentHash]
        if evidence.evidenceType != "text":
            return VerificationObservation.from_text(
                "The evidence type is not supported by the text verifier.",
                evidence_id=evidence.evidenceId,
                capability="text",
                status="unsupported",
                facts={"content_hash": evidence.contentHash},
                weak_signals=[],
                source_refs=source_refs,
                verifier_version=_VERIFIER_VERSION,
                started_at=started_at,
                completed_at=utc_now(),
                error_code="evidence_type_unsupported",
                safe_error_message="The evidence type is not supported by this verifier.",
            )

        text = (evidence.textContent or "").strip()
        lowered = text.lower()
        word_count = _lexical_unit_count(text)
        weak_signals: list[str] = []
        if word_count < _MIN_SPECIFIC_WORDS:
            weak_signals.append("text_too_short")
        if any(phrase in lowered for phrase in _GENERIC_LEARNING_CLAIMS):
            weak_signals.append("generic_learning_claim")
        facts = {
            "has_text": bool(text),
            "character_count": len(text),
            "word_count": word_count,
            "has_concrete_example": any(
                marker in lowered for marker in _EXAMPLE_MARKERS
            ),
            "has_structured_output": bool(_STRUCTURE_RE.search(text)),
            "content_hash": evidence.contentHash,
        }
        payload = {
            "evidence_id": evidence.evidenceId,
            "capability": "text",
            "status": "success",
            "facts": facts,
            "weak_signals": weak_signals,
            "source_refs": source_refs,
            "verifier_version": _VERIFIER_VERSION,
        }
        return VerificationObservation.from_text(
            json.dumps(payload, sort_keys=True),
            evidence_id=evidence.evidenceId,
            capability="text",
            status="success",
            facts=facts,
            weak_signals=weak_signals,
            source_refs=source_refs,
            verifier_version=_VERIFIER_VERSION,
            started_at=started_at,
            completed_at=utc_now(),
        )


class FocusProofTextEvidenceVerificationTool(
    ToolDefinition[EvidenceReferenceAction, VerificationObservation]
):
    name: ClassVar[str] = "focusproof_text_evidence_verification"

    @classmethod
    def annotations_for_focusproof(cls) -> ToolAnnotations:
        return read_only_annotations("FocusProof text evidence verification")

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
                    "Inspect authoritative text evidence loaded by evidence_id. "
                    "Only evidence_id is accepted; never provide evidence text."
                ),
                action_type=EvidenceReferenceAction,
                observation_type=VerificationObservation,
                executor=TextEvidenceVerificationExecutor(repository, session_id),
                annotations=cls.annotations_for_focusproof(),
            )
        ]
