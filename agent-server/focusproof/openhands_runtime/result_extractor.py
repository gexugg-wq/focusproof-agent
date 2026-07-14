from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.base import Event as OpenHandsEvent
from openhands.sdk.llm import TextContent

from focusproof.domain.review import ReviewResult
from focusproof.domain.scoring import score_learning_session
from focusproof.openhands_runtime.handle import (
    ConversationHandle,
    RuntimeReviewResult,
    RuntimeReviewStatus,
)
from focusproof.openhands_runtime.tools.learner_input import (
    LearnerInputAction,
    LearnerInputObservation,
)
from focusproof.openhands_runtime.tools.review_draft import ReviewDraftObservation
from focusproof.openhands_runtime.url_redaction import (
    sanitize_source_refs,
    sanitize_verification_facts,
)
from focusproof.openhands_runtime.tools.verification import VerificationObservation
from focusproof.openhands_runtime.tools.evidence_verification import (
    EvidenceVerificationObservation,
)
from focusproof.persistence.repositories import StoredReview
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.events import Actor, Event, EventType
from focusproof.runtime.observations import Observation


class AuditQuery(Protocol):
    def list(self, session_id: str) -> list[Any]: ...
    def append_final(
        self,
        session_id: str,
        event_type: EventType,
        actor: Actor,
        payload: dict[str, object],
        *,
        event_id: str,
    ) -> Event: ...


class RuntimeResultExtractor:
    def __init__(
        self,
        audit_log: AuditQuery,
        uow_factory: UnitOfWorkFactoryLike | None = None,
    ) -> None:
        self._audit_log = audit_log
        self._uow_factory = uow_factory

    def extract(
        self,
        *,
        handle: ConversationHandle,
        native_events: Sequence[OpenHandsEvent],
        goal: LearningGoal,
        evidence: list[Evidence],
        answers: list[str],
    ) -> RuntimeReviewResult:
        last_answer_index = _last_answer_index(native_events)
        action_by_id = {
            event.id: event
            for event in native_events
            if isinstance(event, ActionEvent)
        }
        waiting = [
            (index, event)
            for index, event in enumerate(native_events)
            if index > last_answer_index
            and isinstance(event, ObservationEvent)
            and isinstance(event.observation, LearnerInputObservation)
        ]
        drafts = [
            (index, event)
            for index, event in enumerate(native_events)
            if index > last_answer_index
            and isinstance(event, ObservationEvent)
            and isinstance(event.observation, ReviewDraftObservation)
            and event.observation.accepted
        ]

        if waiting and (not drafts or waiting[-1][0] > drafts[-1][0]):
            question_event = waiting[-1][1]
            question = question_event.observation
            assert isinstance(question, LearnerInputObservation)
            action_event = action_by_id.get(question_event.action_id)
            requested_type = ""
            if action_event is not None and isinstance(action_event.action, LearnerInputAction):
                requested_type = action_event.action.requested_evidence_type
            questions = [
                {
                    "questionId": question.question_id,
                    "status": question.status,
                    "question": question.question,
                    "reason": question.reason,
                    "requestedEvidenceType": requested_type,
                }
            ]
            self._persist_review(
                handle=handle,
                native_event=question_event,
                review_status="awaiting_user",
                native_event_count=len(native_events),
                score=None,
                result={"agentQuestions": questions},
            )
            return self._result(
                handle=handle,
                native_events=native_events,
                review_status="awaiting_user",
                used=True,
                questions=questions,
            )

        if drafts:
            draft_event = drafts[-1][1]
            observations = _focusproof_observations(
                native_events,
                after_index=last_answer_index,
            )
            review = score_learning_session(
                goal=goal,
                evidence=evidence,
                answers=answers,
                observations=observations,
            )
            self._persist_completed_review(
                handle=handle,
                native_event=draft_event,
                native_event_count=len(native_events),
                review=review,
                evidence_refs=[item.evidenceId for item in evidence],
            )
            return self._result(
                handle=handle,
                native_events=native_events,
                review_status="completed",
                used=True,
                review=review,
            )

        return self._result(
            handle=handle,
            native_events=native_events,
            review_status="failed",
            used=False,
            error="OpenHands run produced neither learner input nor an accepted review draft",
        )

    def _persist_completed_review(
        self,
        *,
        handle: ConversationHandle,
        native_event: ObservationEvent,
        native_event_count: int,
        review: ReviewResult,
        evidence_refs: list[str],
    ) -> None:
        proposed_review_id = f"rev_{uuid4().hex}"
        score_event_id = f"evt_score_{native_event.id}"
        review_event_id = f"evt_review_{native_event.id}"
        score_payload: dict[str, object] = {
            "score": review.score,
            "confidence": review.confidence,
            "status": review.status,
            "dimensions": review.dimensions,
            "findings": [finding.model_dump(mode="json") for finding in review.findings],
            "evidenceRefs": evidence_refs,
        }

        if self._uow_factory is not None:
            record = StoredReview(
                review_id=proposed_review_id,
                session_id=handle.session_id,
                conversation_id=str(handle.conversation_id),
                review_status="completed",
                score=review.score,
                result=review.model_dump(mode="json"),
                native_event_count=native_event_count,
                source_openhands_event_id=native_event.id,
                created_at=datetime.now(UTC),
            )
            with self._uow_factory() as uow:
                stored_review = uow.reviews.add_from_native_event(record)
                stored_score_event = uow.audit_events.append(
                    handle.session_id,
                    "score.calculated",
                    "system",
                    score_payload,
                    source_openhands_event_id=None,
                    event_id=score_event_id,
                )
                uow.audit_events.append(
                    handle.session_id,
                    "review.completed",
                    "system",
                    {
                        "reviewId": stored_review.review_id,
                        "summary": review.summary,
                        "nextStep": review.nextStep,
                        "scoreEventId": stored_score_event.event_id,
                    },
                    source_openhands_event_id=None,
                    event_id=review_event_id,
                )
                uow.commit()
            return

        runtime_score_event = self._audit_log.append_final(
            handle.session_id,
            "score.calculated",
            "system",
            score_payload,
            event_id=score_event_id,
        )
        self._audit_log.append_final(
            handle.session_id,
            "review.completed",
            "system",
            {
                "reviewId": proposed_review_id,
                "summary": review.summary,
                "nextStep": review.nextStep,
                "scoreEventId": runtime_score_event.id,
            },
            event_id=review_event_id,
        )

    def _persist_review(
        self,
        *,
        handle: ConversationHandle,
        native_event: ObservationEvent,
        review_status: str,
        native_event_count: int,
        score: int | None,
        result: dict[str, Any] | None,
    ) -> None:
        if self._uow_factory is None:
            return
        record = StoredReview(
            review_id=f"rev_{uuid4().hex}",
            session_id=handle.session_id,
            conversation_id=str(handle.conversation_id),
            review_status=review_status,
            score=score,
            result=result,
            native_event_count=native_event_count,
            source_openhands_event_id=native_event.id,
            created_at=datetime.now(UTC),
        )
        with self._uow_factory() as uow:
            uow.reviews.add_from_native_event(record)
            uow.commit()

    def _result(
        self,
        *,
        handle: ConversationHandle,
        native_events: Sequence[OpenHandsEvent],
        review_status: RuntimeReviewStatus,
        used: bool,
        questions: list[dict[str, str]] | None = None,
        review: ReviewResult | None = None,
        error: str | None = None,
    ) -> RuntimeReviewResult:
        projected = [
            event
            for event in self._audit_log.list(handle.session_id)
            if event.payload.get("sourceConversationId") == str(handle.conversation_id)
        ]
        return RuntimeReviewResult(
            sessionId=handle.session_id,
            conversationMode=handle.runtime_mode if used else "failed",
            usedOpenHandsConversation=used,
            conversationId=str(handle.conversation_id),
            nativeEventCount=len(native_events),
            messageEventsCount=sum(isinstance(event, MessageEvent) for event in native_events),
            actionEventsCount=sum(isinstance(event, ActionEvent) for event in native_events),
            observationEventsCount=sum(
                isinstance(event, ObservationEvent) for event in native_events
            ),
            projectedEventsCount=len(projected),
            reviewStatus=review_status,
            agentQuestions=questions or [],
            reviewResult=review,
            error=error,
        )


def _last_answer_index(native_events: Sequence[OpenHandsEvent]) -> int:
    latest = -1
    for index, event in enumerate(native_events):
        if isinstance(event, MessageEvent) and _message_kind(event) == "answer":
            latest = index
    return latest


def _message_kind(event: MessageEvent) -> str | None:
    text = "".join(
        item.text for item in event.llm_message.content if isinstance(item, TextContent)
    )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    kind = payload.get("kind") if isinstance(payload, dict) else None
    return kind if isinstance(kind, str) else None


def _focusproof_observations(
    native_events: Sequence[OpenHandsEvent],
    *,
    after_index: int = -1,
) -> list[Observation]:
    observations: list[Observation] = []
    for index, event in enumerate(native_events):
        if index <= after_index:
            continue
        if not isinstance(event, ObservationEvent):
            continue
        native_observation = event.observation
        if isinstance(native_observation, EvidenceVerificationObservation):
            observations.append(
                Observation(
                    toolName=event.tool_name,
                    status="inconclusive",
                    facts={
                        "capability": "legacy",
                        "evidence_type": native_observation.evidence_type,
                        "findings": native_observation.findings,
                        "weak_signals": native_observation.weak_signals,
                        "verifier_version": "legacy",
                    },
                    sourceRefs=sanitize_source_refs(native_observation.source_refs),
                    error=None,
                )
            )
            continue
        if not isinstance(native_observation, VerificationObservation):
            continue
        status = (
            native_observation.status
            if native_observation.status in {"success", "failed", "inconclusive"}
            else "inconclusive"
        )
        observations.append(
            Observation(
                toolName=event.tool_name,
                status=status,
                facts={
                    **sanitize_verification_facts(
                        native_observation.capability,
                        native_observation.facts,
                    ),
                    "capability": native_observation.capability,
                    "weak_signals": native_observation.weak_signals,
                    "verifier_version": native_observation.verifier_version,
                },
                sourceRefs=sanitize_source_refs(native_observation.source_refs),
                error=native_observation.safe_error_message,
            )
        )
    return observations
