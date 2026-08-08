from __future__ import annotations

import json
from typing import Annotated
from typing import Any

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from focusproof.domain.review import ReviewResult
from focusproof.runtime.actions import Action
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.observations import Observation

MAX_DOMAIN_CHARS = 128
MAX_TITLE_CHARS = 512
MAX_GOAL_CHARS = 20_000
MAX_EXPECTED_OUTPUT_CHARS = 20_000
MAX_PLANNED_MINUTES = 525_600
MAX_EVIDENCE_TYPE_CHARS = 128
MAX_EVIDENCE_TEXT_CHARS = 100_000
MAX_URL_CHARS = 2_048
MAX_QUESTION_ID_CHARS = 128
MAX_ANSWER_CHARS = 20_000
MAX_METADATA_BYTES = 16_384
MAX_METADATA_DEPTH = 5
MAX_METADATA_ITEMS = 100

DomainText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_DOMAIN_CHARS),
]
TitleText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_CHARS),
]
GoalText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_GOAL_CHARS),
]
EvidenceTypeText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_EVIDENCE_TYPE_CHARS,
    ),
]
QuestionIdText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_QUESTION_ID_CHARS,
    ),
]


class CreateSessionRequest(BaseModel):
    domain: DomainText
    title: TitleText
    goal: GoalText
    expectedOutput: str | None = Field(
        default=None,
        max_length=MAX_EXPECTED_OUTPUT_CHARS,
    )
    plannedMinutes: int | None = Field(
        default=None,
        ge=1,
        le=MAX_PLANNED_MINUTES,
    )


class SubmitEvidenceRequest(BaseModel):
    evidenceType: EvidenceTypeText
    textContent: str | None = Field(default=None, max_length=MAX_EVIDENCE_TEXT_CHARS)
    sourceUrl: str | None = Field(default=None, max_length=MAX_URL_CHARS)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_shape_and_metadata(self) -> SubmitEvidenceRequest:
        evidence_type = self.evidenceType.casefold()
        if evidence_type == "text" and not (self.textContent or "").strip():
            raise ValueError("text evidence requires non-empty textContent")
        if evidence_type == "url":
            if not (self.sourceUrl or "").strip():
                raise ValueError("URL evidence requires sourceUrl")
            if not (self.textContent or "").strip():
                raise ValueError("URL evidence requires a non-empty explanation")
        metadata_items = _metadata_item_count(self.metadata)
        if metadata_items > MAX_METADATA_ITEMS:
            raise ValueError("metadata contains too many items")
        try:
            encoded = json.dumps(
                self.metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be finite JSON data") from exc
        if len(encoded) > MAX_METADATA_BYTES:
            raise ValueError("metadata is too large")
        return self


class SubmitAnswerRequest(BaseModel):
    questionId: QuestionIdText
    answer: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answer must not be blank")
        return value


class SessionRecord(BaseModel):
    sessionId: str
    status: str
    goal: LearningGoal
    evidence: list[Evidence] = Field(default_factory=list)
    answers: dict[str, str] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)
    previousActions: list[Action] = Field(default_factory=list)
    reviewResult: ReviewResult | None = None
    adapterMode: str


def _metadata_item_count(value: Any, *, depth: int = 1) -> int:
    if depth > MAX_METADATA_DEPTH:
        raise ValueError("metadata is nested too deeply")
    if isinstance(value, dict):
        return len(value) + sum(
            _metadata_item_count(item, depth=depth + 1) for item in value.values()
        )
    if isinstance(value, list):
        return len(value) + sum(
            _metadata_item_count(item, depth=depth + 1) for item in value
        )
    return 0
