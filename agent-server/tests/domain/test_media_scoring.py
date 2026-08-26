from __future__ import annotations

from collections.abc import Sequence

import pytest

from focusproof.domain.scoring import score_learning_session
from focusproof.domain.scoring_inputs import (
    LearningNarrativeProjector,
    VerifiedLearningNarrative,
    narratives_as_evidence,
)
from focusproof.openhands_runtime.result_extractor import _audited_narrative_lineage
from focusproof.media_projection.image_narrative_provider import ImageNarrativeProvider
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.observations import Observation


def _goal() -> LearningGoal:
    return LearningGoal(
        domain="web3",
        title="Understand Monad transactions",
        goal="Explain how a Monad transaction is submitted and confirmed",
    )


def _image_evidence() -> Evidence:
    return Evidence(
        evidenceId="ev_image",
        evidenceType="image/png",
        contentHash="sha256:image",
        metadata={"caption": "learner upload"},
    )


def _media_observation(
    *,
    status: str = "success",
    explanation: str | None,
    visual_facts: Sequence[str] = (),
) -> Observation:
    facts: dict[str, object] = {
        "artifact_ref": "artifact://safe-image-ref",
        "media_type": "image/png",
        "normalized_sha256": "11" * 32,
        "byte_size": 1000,
        "width": 320,
        "height": 200,
    }
    if visual_facts:
        facts["visual_facts"] = list(visual_facts)
    if explanation is not None:
        facts["learner_explanation"] = explanation
    return Observation(
        toolName="focusproof_media_evidence_verification",
        status=status,  # type: ignore[arg-type]
        sourceEventId="obs-native-image-1" if visual_facts else None,
        facts=facts,
        sourceRefs=["ev_image", "artifact://safe-image-ref"],
    )


def _review_from_image(
    *,
    explanation: str | None,
    observation_status: str = "success",
    answers: Sequence[str] = (),
    visual_facts: Sequence[str] = (),
):
    evidence = [_image_evidence()]
    observation = _media_observation(
        status=observation_status,
        explanation=explanation,
        visual_facts=visual_facts,
    )
    narratives = LearningNarrativeProjector(providers=(ImageNarrativeProvider(),)).project_all(
        evidence, [observation]
    )
    return score_learning_session(
        _goal(),
        narratives_as_evidence(evidence, narratives),
        list(answers),
        [observation],
    )


def test_image_visual_facts_produce_stable_lineage_in_scoring_evidence() -> None:
    evidence = [_image_evidence()]
    observation = _media_observation(
        explanation="The learner inspected the transaction screenshot.",
        visual_facts=(
            "A signed Monad transaction includes a nonce.",
            "The interface displays a gas limit before submission.",
            "A confirmed result is included in a block.",
        ),
    )
    projector = LearningNarrativeProjector(providers=(ImageNarrativeProvider(),))

    first = projector.project_all(evidence, [observation])
    second = projector.project_all(evidence, [observation])
    converted = narratives_as_evidence(evidence, first)

    assert first == second
    assert len(first) == 1
    assert len(first[0].consumed_fact_ids) == 3
    assert first[0].source_observation_event_id == "obs-native-image-1"
    assert first[0].projection_id
    assert "signed Monad transaction" in first[0].text
    assert "gas limit" in first[0].text
    scoring_input = converted[-1]
    assert scoring_input.metadata["consumedFactIds"] == list(first[0].consumed_fact_ids)
    assert scoring_input.metadata["sourceObservationEventId"] == ("obs-native-image-1")
    assert scoring_input.metadata["narrativeProjectionId"] == first[0].projection_id


def test_image_without_verified_explanation_stays_weak() -> None:
    result = _review_from_image(explanation=None)

    assert result.score < 60
    assert result.status == "WeakEvidence"


def test_failed_image_observation_stays_weak_even_if_facts_contain_text() -> None:
    result = _review_from_image(
        explanation="A Monad transaction is submitted and confirmed in a block.",
        observation_status="failed",
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"


def test_image_explanation_that_copies_goal_is_not_learning() -> None:
    result = _review_from_image(
        explanation="Explain how a Monad transaction is submitted and confirmed",
        answers=("A nonce orders account transactions.",),
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"


def test_irrelevant_image_explanation_is_not_goal_aligned() -> None:
    result = _review_from_image(
        explanation="I made a sandwich and cleaned my desk after lunch.",
        answers=("The sandwich had bread and tomato.",),
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"


def test_aligned_image_explanation_and_answer_can_support_learning_without_image_scoring_branch() -> (
    None
):
    result = _review_from_image(
        explanation=(
            "A Monad transaction uses a signed request with a nonce and gas limit, "
            "then validators execute it and include the confirmed result in a block."
        ),
        answers=("The nonce orders account transactions, and gas bounds the execution cost.",),
        visual_facts=(
            "A signed Monad transaction includes a nonce.",
            "The interface displays a gas limit before submission.",
            "A confirmed result is included in a block.",
        ),
    )

    assert result.score >= 60
    assert result.status == "LikelyLearning"


class FakeAudioProvider:
    def project(
        self,
        evidence: Evidence,
        observations: Sequence[Observation],
    ) -> VerifiedLearningNarrative | None:
        if evidence.evidenceType != "audio/wav":
            return None
        if not any(
            observation.status == "success" and evidence.evidenceId in observation.sourceRefs
            for observation in observations
        ):
            return None
        return VerifiedLearningNarrative(
            evidence_id=evidence.evidenceId,
            text=(
                "The learner explained that a Monad transaction uses a nonce, gas, "
                "execution, and block confirmation."
            ),
            verification_status="success",
        )


def test_fake_audio_provider_uses_same_narrative_scoring_input() -> None:
    evidence = [
        Evidence(
            evidenceId="ev_audio",
            evidenceType="audio/wav",
            contentHash="sha256:audio",
        )
    ]
    observations = [
        Observation(
            toolName="focusproof_audio_evidence_verification",
            status="success",
            facts={"duration_seconds": 30},
            sourceRefs=["ev_audio"],
        )
    ]
    narratives = LearningNarrativeProjector(providers=(FakeAudioProvider(),)).project_all(
        evidence, observations
    )

    result = score_learning_session(
        _goal(),
        narratives_as_evidence(evidence, narratives),
        ["The nonce prevents replay and gas constrains computation."],
        observations,
    )

    assert result.score >= 60
    assert result.status == "LikelyLearning"


def test_audited_narrative_lineage_is_explicit_and_canonically_aggregated() -> None:
    narrative = VerifiedLearningNarrative(
        evidence_id="ev_image",
        text="safe projected learning narrative",
        verification_status="success",
        consumed_fact_ids=("fact-b", "fact-a"),
        consumed_fact_text_digests=("b" * 64, "a" * 64),
        source_observation_event_id="obs-1",
        projection_id="projection-1",
    )

    lineage, aggregate = _audited_narrative_lineage((narrative,))

    assert lineage[0]["consumedFactIds"] == ["fact-b", "fact-a"]
    assert {fact["factId"] for fact in lineage[0]["facts"]} == {"fact-a", "fact-b"}
    assert aggregate == ["fact-a", "fact-b"]


def test_audited_narrative_lineage_emits_nothing_for_explanation_only() -> None:
    narrative = VerifiedLearningNarrative(
        evidence_id="ev_image",
        text="explanation only",
        verification_status="success",
    )

    assert _audited_narrative_lineage((narrative,)) == ([], [])


def test_audited_narrative_lineage_rejects_duplicate_or_mismatched_facts() -> None:
    duplicate = VerifiedLearningNarrative(
        evidence_id="ev_image",
        text="safe narrative",
        verification_status="success",
        consumed_fact_ids=("fact-a", "fact-a"),
        consumed_fact_text_digests=("a" * 64, "b" * 64),
        source_observation_event_id="obs-1",
        projection_id="projection-1",
    )
    mismatch = VerifiedLearningNarrative(
        evidence_id="ev_image",
        text="safe narrative",
        verification_status="success",
        consumed_fact_ids=("fact-a",),
        consumed_fact_text_digests=(),
        source_observation_event_id="obs-1",
        projection_id="projection-1",
    )

    for narrative in (duplicate, mismatch):
        with pytest.raises(ValueError, match="lineage"):
            _audited_narrative_lineage((narrative,))


@pytest.mark.parametrize("duplicate_kind", ["fact", "projection"])
def test_audited_narrative_lineage_rejects_cross_narrative_duplicates(
    duplicate_kind: str,
) -> None:
    first = VerifiedLearningNarrative(
        evidence_id="ev-1",
        text="first safe narrative",
        verification_status="success",
        consumed_fact_ids=("fact-1",),
        consumed_fact_text_digests=("1" * 64,),
        source_observation_event_id="obs-1",
        projection_id="projection-shared" if duplicate_kind == "projection" else "projection-1",
    )
    second = VerifiedLearningNarrative(
        evidence_id="ev-2",
        text="second safe narrative",
        verification_status="success",
        consumed_fact_ids=("fact-1" if duplicate_kind == "fact" else "fact-2",),
        consumed_fact_text_digests=("2" * 64,),
        source_observation_event_id="obs-2",
        projection_id="projection-shared" if duplicate_kind == "projection" else "projection-2",
    )

    with pytest.raises(ValueError, match="duplicate"):
        _audited_narrative_lineage((first, second))


@pytest.mark.parametrize("bad_id", [7, None, "", " leading", "trailing "])
def test_audited_narrative_lineage_rejects_non_strict_ids(bad_id: object) -> None:
    narrative = VerifiedLearningNarrative(
        evidence_id="ev-1",
        text="safe narrative",
        verification_status="success",
        consumed_fact_ids=(bad_id,),  # type: ignore[arg-type]
        consumed_fact_text_digests=("1" * 64,),
        source_observation_event_id="obs-1",
        projection_id="projection-1",
    )

    with pytest.raises(ValueError, match="lineage"):
        _audited_narrative_lineage((narrative,))
