from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol

from focusproof.runtime.evidence import Evidence
from focusproof.runtime.observations import Observation

NarrativeStatus = Literal["success", "failed", "inconclusive"]
_MAX_NARRATIVE_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class VerifiedLearningNarrative:
    evidence_id: str
    text: str
    verification_status: NarrativeStatus
    consumed_fact_ids: tuple[str, ...] = ()
    consumed_fact_text_digests: tuple[str, ...] = ()
    source_observation_event_id: str = ""
    projection_id: str = ""

    def __post_init__(self) -> None:
        evidence_id = self.evidence_id.strip()
        text = " ".join(self.text.split())
        if not evidence_id:
            raise ValueError("evidence_id must not be empty")
        if self.verification_status not in {"success", "failed", "inconclusive"}:
            raise ValueError("verification_status is invalid")
        if self.verification_status == "success" and not text:
            raise ValueError("successful narrative text must not be empty")
        object.__setattr__(self, "evidence_id", evidence_id)
        object.__setattr__(self, "text", text[:_MAX_NARRATIVE_CHARS])
        object.__setattr__(self, "consumed_fact_ids", tuple(self.consumed_fact_ids))
        object.__setattr__(
            self,
            "consumed_fact_text_digests",
            tuple(self.consumed_fact_text_digests),
        )


class ReviewCompletionPolicy(Protocol):
    def failure_reason(
        self,
        evidence: Sequence[Evidence],
        observations: Sequence[Observation],
    ) -> str | None: ...


class LearningNarrativeProjectionProvider(Protocol):
    def project(
        self,
        evidence: Evidence,
        observations: Sequence[Observation],
    ) -> VerifiedLearningNarrative | None: ...


class LearningNarrativeProjector:
    def __init__(
        self,
        *,
        providers: Sequence[LearningNarrativeProjectionProvider] = (),
    ) -> None:
        self._providers = tuple(providers)

    def project_all(
        self,
        evidence: Sequence[Evidence],
        observations: Sequence[Observation],
    ) -> tuple[VerifiedLearningNarrative, ...]:
        by_evidence_id: dict[str, VerifiedLearningNarrative] = {}
        for item in evidence:
            for provider in self._providers:
                projected = provider.project(item, observations)
                if projected is None:
                    continue
                existing = by_evidence_id.get(projected.evidence_id)
                if existing is not None and existing != projected:
                    raise ValueError(f"conflicting learning narrative: {projected.evidence_id}")
                by_evidence_id[projected.evidence_id] = projected
        return tuple(by_evidence_id.values())


def narratives_as_evidence(
    evidence: Sequence[Evidence],
    narratives: Sequence[VerifiedLearningNarrative],
) -> list[Evidence]:
    """Append successful verified narratives as generic text evidence.

    The scoring implementation remains modality-neutral: image/audio/etc. providers
    project verified facts into this generic text input instead of adding modality
    branches to domain scoring.
    """

    converted = list(evidence)
    for narrative in narratives:
        if narrative.verification_status != "success":
            continue
        digest = sha256(f"{narrative.evidence_id}\n{narrative.text}".encode("utf-8")).hexdigest()
        converted.append(
            Evidence(
                evidenceId=narrative.evidence_id,
                evidenceType="text",
                contentHash=f"sha256:narrative:{digest}",
                textContent=narrative.text,
                metadata={
                    "narrativeSource": "verified_learning_narrative",
                    "verificationStatus": narrative.verification_status,
                    **(
                        {
                            "consumedFactIds": list(narrative.consumed_fact_ids),
                            "sourceObservationEventId": narrative.source_observation_event_id,
                            "narrativeProjectionId": narrative.projection_id,
                        }
                        if narrative.consumed_fact_ids
                        else {}
                    ),
                },
            )
        )
    return converted
