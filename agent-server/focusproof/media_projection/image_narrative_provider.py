from __future__ import annotations

from collections.abc import Sequence

from focusproof.domain.scoring_inputs import VerifiedLearningNarrative
from focusproof.media_projection.visual_fact_identity import (
    VISUAL_FACT_TOOL_NAME,
    derive_visual_fact_identities,
    derive_visual_projection_id,
    normalize_visual_fact_texts,
)
from focusproof.runtime.evidence import Evidence
from focusproof.runtime.observations import Observation

_MAX_EXPLANATION_CHARS = 2_000


class ImageNarrativeProvider:
    def project(
        self,
        evidence: Evidence,
        observations: Sequence[Observation],
    ) -> VerifiedLearningNarrative | None:
        if not evidence.evidenceType.strip().lower().startswith("image/"):
            return None
        for observation in observations:
            if observation.status != "success":
                continue
            if evidence.evidenceId not in observation.sourceRefs:
                continue
            if (
                observation.toolName != VISUAL_FACT_TOOL_NAME
                and observation.facts.get("capability") != "image"
            ):
                continue
            source_event_id = observation.sourceEventId
            visual_facts = observation.facts.get("visual_facts")
            explanation = observation.facts.get("learner_explanation")
            explanation_text = " ".join(explanation.split()) if isinstance(explanation, str) else ""
            normalized_visual_facts = normalize_visual_fact_texts(visual_facts)
            if not normalized_visual_facts:
                if not explanation_text:
                    continue
                return VerifiedLearningNarrative(
                    evidence_id=evidence.evidenceId,
                    text=explanation_text[:_MAX_EXPLANATION_CHARS],
                    verification_status="success",
                )
            if not isinstance(source_event_id, str) or not source_event_id:
                continue
            fact_identities = derive_visual_fact_identities(
                evidence.evidenceId,
                source_event_id,
                normalized_visual_facts,
            )
            fact_ids = tuple(fact.fact_id for fact in fact_identities)
            text_digests = tuple(fact.text_digest for fact in fact_identities)
            text = " ".join(
                part
                for part in (
                    explanation_text,
                    "Verified visual facts: "
                    + "; ".join(fact.normalized_text for fact in fact_identities),
                )
                if part
            )
            projection_id = derive_visual_projection_id(
                evidence.evidenceId,
                source_event_id,
                fact_ids,
            )
            return VerifiedLearningNarrative(
                evidence_id=evidence.evidenceId,
                text=text[:_MAX_EXPLANATION_CHARS],
                verification_status="success",
                consumed_fact_ids=fact_ids,
                consumed_fact_text_digests=text_digests,
                source_observation_event_id=source_event_id,
                projection_id=projection_id,
            )
        return None


class ImageVerificationCompletionPolicy:
    def failure_reason(
        self,
        evidence: Sequence[Evidence],
        observations: Sequence[Observation],
    ) -> str | None:
        media_ids = {
            item.evidenceId
            for item in evidence
            if item.evidenceType.strip().lower().startswith("image/")
        }
        if not media_ids:
            return None
        verified: set[str] = set()
        for observation in observations:
            if observation.status != "success":
                continue
            if observation.facts.get("capability") != "image":
                continue
            visual_facts = observation.facts.get("visual_facts")
            if not isinstance(visual_facts, list):
                continue
            concrete = [item for item in visual_facts if isinstance(item, str) and item.strip()]
            if len(concrete) < 3:
                continue
            verified.update(media_ids.intersection(observation.sourceRefs))
        if verified == media_ids:
            return None
        return "Media evidence lacks at least three verified native visual facts."
