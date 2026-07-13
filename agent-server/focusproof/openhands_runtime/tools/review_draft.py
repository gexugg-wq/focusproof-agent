from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, ClassVar, Self
from uuid import uuid4

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)
from pydantic import Field

from focusproof.openhands_runtime.tools import read_only_annotations


class ReviewDraftAction(Action):
    credibility_findings: list[str]
    understanding_findings: list[str]
    contradictions: list[str]
    recommended_next_step: str
    confidence: float = Field(ge=0.0, le=1.0)


class ReviewDraftObservation(Observation):
    accepted: bool = True
    draft_id: str
    credibility_findings: list[str]
    understanding_findings: list[str]
    contradictions: list[str]
    recommended_next_step: str
    confidence: float


class ReviewDraftExecutor(ToolExecutor[ReviewDraftAction, ReviewDraftObservation]):
    def __call__(
        self,
        action: ReviewDraftAction,
        conversation: Any | None = None,
    ) -> ReviewDraftObservation:
        del conversation
        credibility_findings = _clean(action.credibility_findings)
        understanding_findings = _clean(action.understanding_findings)
        contradictions = _clean(action.contradictions)
        recommended_next_step = action.recommended_next_step.strip()
        payload = {
            "accepted": True,
            "draft_id": f"draft_{uuid4().hex}",
            "credibility_findings": credibility_findings,
            "understanding_findings": understanding_findings,
            "contradictions": contradictions,
            "recommended_next_step": recommended_next_step,
            "confidence": action.confidence,
        }
        return ReviewDraftObservation.from_text(
            json.dumps(payload, sort_keys=True),
            accepted=True,
            draft_id=str(payload["draft_id"]),
            credibility_findings=credibility_findings,
            understanding_findings=understanding_findings,
            contradictions=contradictions,
            recommended_next_step=recommended_next_step,
            confidence=action.confidence,
        )


class FocusProofReviewDraftTool(ToolDefinition[ReviewDraftAction, ReviewDraftObservation]):
    name: ClassVar[str] = "focusproof_review_draft"

    @classmethod
    def annotations_for_focusproof(cls) -> ToolAnnotations:
        return read_only_annotations("FocusProof review draft")

    @classmethod
    def create(
        cls,
        conv_state: Any | None = None,
        *,
        session_id: str | None = None,
    ) -> Sequence[Self]:
        del conv_state, session_id
        return [
            cls(
                description=(
                    "Submit structured review findings without assigning a final numeric score."
                ),
                action_type=ReviewDraftAction,
                observation_type=ReviewDraftObservation,
                executor=ReviewDraftExecutor(),
                annotations=cls.annotations_for_focusproof(),
            )
        ]


def _clean(values: list[str]) -> list[str]:
    return [value.strip() for value in values if value.strip()]
