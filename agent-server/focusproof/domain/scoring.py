from __future__ import annotations

import re

from focusproof.domain.review import Finding, ReviewResult, ReviewStatus
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.observations import Observation

_GENERIC_PHRASES = (
    "learned many things",
    "studied a lot",
    "learned a lot",
    "many things",
    "??",
)
_CONCEPT_TERMS = (
    "nonce",
    "gas",
    "signature",
    "sender",
    "receiver",
    "block",
    "confirm",
    "confirmed",
    "event",
    "append",
    "replay",
    "view",
    "immutable",
    "transaction",
)
_TX_RE = re.compile(r"0x[a-fA-F0-9]{8,}")


def _text(evidence: Evidence) -> str:
    return (evidence.textContent or "").strip()


def _is_generic(text: str) -> bool:
    lowered = text.lower()
    return len(text.split()) < 9 or any(phrase in lowered for phrase in _GENERIC_PHRASES)


def _concept_count(text: str) -> int:
    lowered = text.lower()
    return sum(1 for term in _CONCEPT_TERMS if term in lowered)


def _dimensions(score: int, understanding: int) -> dict[str, int]:
    return {
        "goalClarity": min(15, max(5, score // 8)),
        "evidenceSpecificity": min(20, max(0, score // 4)),
        "goalAlignment": min(20, max(0, score // 5)),
        "understanding": min(25, understanding),
        "output": min(10, max(0, score // 10)),
        "reflection": min(10, max(0, score // 12)),
    }


def score_learning_session(
    goal: LearningGoal,
    evidence: list[Evidence],
    answers: list[str],
    observations: list[Observation] | None = None,
) -> ReviewResult:
    del goal
    observations = observations or []
    findings: list[Finding] = []
    if not evidence:
        return ReviewResult(
            status="InsufficientEvidence",
            score=15,
            confidence=0.3,
            dimensions=_dimensions(15, 0),
            findings=[Finding(severity="warning", message="No evidence was submitted.")],
            summary="There is not enough learning evidence to review.",
            nextStep="Submit concrete notes, outputs, or artifacts from the learning session.",
        )

    joined_text = " ".join(_text(item) for item in evidence)
    answer_text = " ".join(answers)
    first_id = evidence[0].evidenceId
    has_tx = any(item.evidenceType == "transaction" or _TX_RE.search(_text(item)) for item in evidence)
    concepts = _concept_count(joined_text) + _concept_count(answer_text)
    has_specific_text = any(item.evidenceType == "text" and not _is_generic(_text(item)) for item in evidence)
    has_answer = bool(answer_text.strip())

    if all(_is_generic(_text(item)) for item in evidence if item.evidenceType == "text") and not has_tx:
        findings.append(
            Finding(
                severity="warning",
                message="Evidence is generic and does not show specific concepts or outputs.",
                evidenceIds=[first_id],
            )
        )
        return ReviewResult(
            status="WeakEvidence",
            score=35,
            confidence=0.45,
            dimensions=_dimensions(35, 4),
            findings=findings,
            summary="The submitted text is too broad to establish credible learning.",
            nextStep="Add concrete concepts, examples, and explain what changed in your understanding.",
        )

    if has_tx and not has_answer and concepts < 2:
        findings.append(
            Finding(
                severity="warning",
                message="A transaction-shaped artifact exists, but it does not prove understanding by itself.",
                evidenceIds=[first_id],
                observationRefs=[obs.toolName for obs in observations],
            )
        )
        return ReviewResult(
            status="NeedsMoreVerification",
            score=50,
            confidence=0.5,
            dimensions=_dimensions(50, 8),
            findings=findings,
            summary="The transaction evidence needs an explanation before it can support learning.",
            nextStep="Explain what the transaction did, why it mattered, and what each key field means.",
        )

    score = 45
    understanding = 8
    if has_specific_text:
        score += 12
        understanding += 6
    if concepts >= 3:
        score += 10
        understanding += 6
    if has_answer:
        score += 10
        understanding += 6
    if has_tx:
        score += 3
    final_score = min(score, 82)
    status: ReviewStatus = "LikelyLearning" if final_score >= 60 else "WeakEvidence"
    findings.append(
        Finding(
            severity="info",
            message="Evidence and learner explanation contain specific concepts tied to the goal.",
            evidenceIds=[item.evidenceId for item in evidence],
            observationRefs=[obs.toolName for obs in observations],
        )
    )
    return ReviewResult(
        status=status,
        score=final_score,
        confidence=0.72 if final_score >= 60 else 0.55,
        dimensions=_dimensions(final_score, understanding),
        findings=findings,
        summary="The session shows credible learning evidence with explainable details.",
        nextStep="Preserve the evidence and add one reflection about what to practice next.",
    )
