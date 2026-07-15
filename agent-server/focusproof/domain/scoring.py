from __future__ import annotations

import re
import unicodedata
from collections import Counter

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
_NEAR_COPY_MIN_GOAL_COVERAGE = 0.8
_NEAR_COPY_MAX_EVIDENCE_NOVELTY = 0.2
_NEAR_COPY_MAX_GOAL_OMISSION = 0.2


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


def _normalized_copy_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _copy_comparison_units(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    units: list[str] = []
    word: list[str] = []

    def flush_word() -> None:
        if word:
            units.append("".join(word))
            word.clear()

    for character in normalized:
        if _CJK_CHARACTER_RE.fullmatch(character):
            flush_word()
            units.append(character)
        elif character.isalnum():
            word.append(character)
        else:
            flush_word()
    flush_word()
    return units


def _is_near_copy(candidate: str, source: str) -> bool:
    normalized_candidate = _normalized_copy_text(candidate)
    normalized_source = _normalized_copy_text(source)
    if not normalized_candidate or not normalized_source:
        return False
    if normalized_candidate == normalized_source:
        return True

    candidate_units = _copy_comparison_units(candidate)
    source_units = _copy_comparison_units(source)
    if not candidate_units or not source_units:
        return False
    candidate_counts = Counter(candidate_units)
    source_counts = Counter(source_units)
    shared_count = sum((candidate_counts & source_counts).values())
    goal_coverage = shared_count / len(source_units)
    evidence_novelty = (len(candidate_units) - shared_count) / len(candidate_units)
    goal_omission = (len(source_units) - shared_count) / len(source_units)
    return (
        goal_coverage >= _NEAR_COPY_MIN_GOAL_COVERAGE
        and evidence_novelty <= _NEAR_COPY_MAX_EVIDENCE_NOVELTY
        and goal_omission <= _NEAR_COPY_MAX_GOAL_OMISSION
    )


def _restates_goal_without_new_information(goal: LearningGoal, text: str) -> bool:
    return _is_near_copy(text, goal.goal) or _is_near_copy(
        text,
        f"{goal.title} {goal.goal}",
    )


def _has_novel_information(candidate: str, source: str) -> bool:
    candidate_units = _copy_comparison_units(candidate)
    source_units = _copy_comparison_units(source)
    if not candidate_units:
        return False
    shared_count = sum(
        (Counter(candidate_units) & Counter(source_units)).values()
    )
    novelty = (len(candidate_units) - shared_count) / len(candidate_units)
    return novelty > _NEAR_COPY_MAX_EVIDENCE_NOVELTY


def _has_basic_goal_association(goal: LearningGoal, text: str) -> bool:
    goal_text = f"{goal.title} {goal.goal}"
    if _meaningful_terms(goal_text) & _meaningful_terms(text):
        return True
    goal_cjk = set(_CJK_CHARACTER_RE.findall(goal_text))
    text_cjk = set(_CJK_CHARACTER_RE.findall(text))
    return len(goal_cjk & text_cjk) >= 2


def _answer_adds_limited_support(goal: LearningGoal, answer: str) -> bool:
    if not _has_specific_answer(answer):
        return False
    if _restates_goal_without_new_information(goal, answer):
        return False
    if not _has_novel_information(answer, f"{goal.title} {goal.goal}"):
        return False
    return _has_basic_goal_association(goal, answer)


def _is_specific_goal_aligned_text(goal: LearningGoal, evidence: Evidence) -> bool:
    text = _text(evidence)
    return not _is_generic(text) and _has_basic_goal_association(goal, text)


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

    answer_text = " ".join(answers)
    first_id = evidence[0].evidenceId
    has_specific_answer = _has_specific_answer(answer_text)
    answer_adds_limited_support = _answer_adds_limited_support(goal, answer_text)
    has_successful_verification = any(
        observation.status == "success" for observation in observations
    )
    text_items = [item for item in evidence if item.evidenceType == "text"]
    copied_text_items = [
        item
        for item in text_items
        if _restates_goal_without_new_information(goal, _text(item))
    ]
    copied_evidence_ids = {item.evidenceId for item in copied_text_items}
    has_independent_aligned_specific_text = any(
        item.evidenceId not in copied_evidence_ids
        and _is_specific_goal_aligned_text(goal, item)
        for item in text_items
    )
    if copied_text_items and not has_independent_aligned_specific_text:
        copied_only_score = 50 if answer_adds_limited_support else 35
        copied_only_understanding = 10 if answer_adds_limited_support else 4
        findings.append(
            Finding(
                severity="warning",
                message=(
                    "The submitted text restates the learning goal. A related, specific answer "
                    "can provide limited support, but copied evidence cannot establish learning "
                    "without a related, specific, independent output."
                ),
                evidenceIds=[item.evidenceId for item in copied_text_items],
            )
        )
        return ReviewResult(
            status="WeakEvidence",
            score=copied_only_score,
            confidence=0.55 if answer_adds_limited_support else 0.45,
            dimensions=_dimensions(copied_only_score, copied_only_understanding),
            findings=findings,
            summary=(
                "Restating the learning goal is not independent evidence of learning."
            ),
            nextStep=(
                "Add a concrete explanation, worked example, reflection, or independent output."
            ),
        )

    has_specific_aligned_text = any(
        item.evidenceId not in copied_evidence_ids
        and _is_specific_goal_aligned_text(goal, item)
        for item in text_items
    )
    has_goal_alignment = has_specific_aligned_text or (
        has_specific_answer and _has_basic_goal_association(goal, answer_text)
    )

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
    if has_specific_aligned_text:
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
