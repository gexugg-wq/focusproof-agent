from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, ClassVar, Literal, Self
from uuid import NAMESPACE_URL, uuid5

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)

from focusproof.openhands_runtime.tools import read_only_annotations


class LearnerInputAction(Action):
    question: str
    reason: str
    requested_evidence_type: str


class LearnerInputObservation(Observation):
    question_id: str
    status: Literal["awaiting_user"] = "awaiting_user"
    question: str
    reason: str


class LearnerInputExecutor(ToolExecutor[LearnerInputAction, LearnerInputObservation]):
    def __call__(
        self,
        action: LearnerInputAction,
        conversation: Any | None = None,
    ) -> LearnerInputObservation:
        del conversation
        payload = {
            "question_id": question_id_for(action),
            "status": "awaiting_user",
            "question": action.question.strip(),
            "reason": action.reason.strip(),
        }
        return LearnerInputObservation.from_text(
            json.dumps(payload, sort_keys=True),
            question_id=payload["question_id"],
            status="awaiting_user",
            question=payload["question"],
            reason=payload["reason"],
        )


class FocusProofLearnerInputTool(ToolDefinition[LearnerInputAction, LearnerInputObservation]):
    name: ClassVar[str] = "focusproof_learner_input"

    @classmethod
    def annotations_for_focusproof(cls) -> ToolAnnotations:
        return read_only_annotations("FocusProof learner input")

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
                description="Ask the learner one focused question when review facts are insufficient.",
                action_type=LearnerInputAction,
                observation_type=LearnerInputObservation,
                executor=LearnerInputExecutor(),
                annotations=cls.annotations_for_focusproof(),
            )
        ]


def question_id_for(action: LearnerInputAction) -> str:
    identity = json.dumps(
        action.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"q_{uuid5(NAMESPACE_URL, identity).hex}"
