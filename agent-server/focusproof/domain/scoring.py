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
_TERM_RE = re.compile(r"[^\W\d_]{4,}", re.UNICODE)
_CJK_CHARACTER_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)
_NON_CJK_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)
_GOAL_STOP_WORDS = {
    "about",
    "explain",
    "learn",
    "understand",
    "using",
    "with",
}


def _text(evidence: Evidence) -> str:
    return (evidence.textContent or "").strip()


def _is_generic(text: str) -> bool:
    lowered = text.lower()
    return _lexical_unit_count(text) < 9 or any(
        phrase in lowered for phrase in _GENERIC_PHRASES
    )


def _lexical_unit_count(text: str) -> int:
    cjk_count = len(_CJK_CHARACTER_RE.findall(text))
    if cjk_count == 0:
        return len(text.split())
    non_cjk_text = _CJK_CHARACTER_RE.sub(" ", text)
    return cjk_count + len(_NON_CJK_WORD_RE.findall(non_cjk_text))


def _has_specific_answer(text: str) -> bool:
    lowered = text.lower()
    return _lexical_unit_count(text) >= 5 and not any(
        phrase in lowered for phrase in _GENERIC_PHRASES
    )


def _meaningful_terms(text: str) -> set[str]:
    return {
        term
        for term in _TERM_RE.findall(text.lower())
        if term not in _GOAL_STOP_WORDS
    }


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
    has_specific_text = any(item.evidenceType == "text" and not _is_generic(_text(item)) for item in evidence)
    has_specific_answer = _has_specific_answer(answer_text)
    goal_terms = _meaningful_terms(f"{goal.title} {goal.goal}")
    submitted_terms = _meaningful_terms(f"{joined_text} {answer_text}")
    has_goal_alignment = bool(goal_terms & submitted_terms)
    has_successful_verification = any(
        observation.status == "success" for observation in observations
    )

    text_items = [item for item in evidence if item.evidenceType == "text"]
    if (
        text_items
        and all(_is_generic(_text(item)) for item in text_items)
        and not has_specific_answer
        and not has_successful_verification
    ):
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

    score = 45
    understanding = 8
    if has_specific_text:
        score += 12
        understanding += 6
    if has_goal_alignment:
        score += 8
        understanding += 4
    if has_specific_answer:
        score += 10
        understanding += 6
    final_score = min(score, 82)
    status: ReviewStatus = "LikelyLearning" if final_score >= 60 else "WeakEvidence"
    findings.append(
        Finding(
            severity="info",
            message="Evidence and learner explanation contain domain-neutral specific details.",
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
