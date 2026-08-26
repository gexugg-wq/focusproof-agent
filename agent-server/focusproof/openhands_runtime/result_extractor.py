from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, TypeGuard
from uuid import uuid4

from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.base import Event as OpenHandsEvent
from openhands.sdk.llm import TextContent

from focusproof.domain.review import ReviewResult
from focusproof.domain.scoring import score_learning_session
from focusproof.domain.scoring_inputs import (
    LearningNarrativeProjectionProvider,
    LearningNarrativeProjector,
    ReviewCompletionPolicy,
    narratives_as_evidence,
)
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
from focusproof.openhands_runtime.tools.verification import (
    EvidenceReferenceAction,
    VerificationObservation,
)
from focusproof.openhands_runtime.tools.evidence_verification import (
    EvidenceVerificationObservation,
)
from focusproof.persistence.repositories import StoredReview
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.events import Event as AuditEvent
from focusproof.runtime.observations import Observation
from focusproof.runtime.audit_projection import AuditQuery


class _RuntimeResultExtractor:
    def __init__(
        self,
        audit_log: AuditQuery,
        uow_factory: UnitOfWorkFactoryLike | None = None,
        *,
        narrative_providers: Sequence[LearningNarrativeProjectionProvider] = (),
        completion_policies: Sequence[ReviewCompletionPolicy] = (),
    ) -> None:
        self._audit_log = audit_log
        self._uow_factory = uow_factory
        self._narrative_projector = LearningNarrativeProjector(
            providers=narrative_providers,
        )
        self._completion_policies = tuple(completion_policies)

    def _extract_managed(
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
            event.id: event for event in native_events if isinstance(event, ActionEvent)
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
            draft_index, draft_event = drafts[-1]
            observations = _focusproof_observations(
                native_events,
                after_index=_last_accepted_draft_index(native_events, before_index=draft_index),
                before_index=draft_index,
            )
            failure_reason = next(
                (
                    reason
                    for policy in self._completion_policies
                    if (reason := policy.failure_reason(evidence, observations)) is not None
                ),
                None,
            )
            if failure_reason is not None:
                return self._result(
                    handle=handle,
                    native_events=native_events,
                    review_status="failed",
                    used=True,
                    error=failure_reason,
                )
            narratives = self._narrative_projector.project_all(evidence, observations)
            review = score_learning_session(
                goal=goal,
                evidence=narratives_as_evidence(evidence, narratives),
                answers=answers,
                observations=observations,
            )
            narrative_lineage, consumed_fact_ids = _audited_narrative_lineage(narratives)
            self._persist_completed_review(
                handle=handle,
                native_event=draft_event,
                native_event_count=len(native_events),
                review=review,
                evidence_refs=[item.evidenceId for item in evidence],
                narrative_lineage=narrative_lineage,
                consumed_fact_ids=consumed_fact_ids,
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
        narrative_lineage: list[dict[str, object]],
        consumed_fact_ids: list[str],
    ) -> None:
        proposed_review_id = f"rev_{uuid4().hex}"
        source_observation_event_id = str(native_event.id)
        score_event_id = f"evt_score_{source_observation_event_id}"
        review_event_id = f"evt_review_{source_observation_event_id}"
        score_payload: dict[str, object] = {
            "score": review.score,
            "confidence": review.confidence,
            "status": review.status,
            "dimensions": review.dimensions,
            "findings": [finding.model_dump(mode="json") for finding in review.findings],
            "evidenceRefs": evidence_refs,
            "sourceObservationEventId": source_observation_event_id,
            "narrativeLineage": narrative_lineage,
            "consumedFactIds": consumed_fact_ids,
        }

        if self._uow_factory is not None:
            record = StoredReview(
                review_id=proposed_review_id,
                session_id=handle.session_id,
                conversation_id=handle.conversation_id.hex,
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
                        "sourceObservationEventId": source_observation_event_id,
                        **(
                            {
                                "narrativeProjectionIds": [
                                    item["projectionId"] for item in narrative_lineage
                                ]
                            }
                            if narrative_lineage
                            else {}
                        ),
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
                "sourceObservationEventId": source_observation_event_id,
                **(
                    {"narrativeProjectionIds": [item["projectionId"] for item in narrative_lineage]}
                    if narrative_lineage
                    else {}
                ),
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
            conversation_id=handle.conversation_id.hex,
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
            conversationId=handle.conversation_id.hex,
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


def _audited_narrative_lineage(
    narratives: Sequence[object],
) -> tuple[list[dict[str, object]], list[str]]:
    lineage: list[dict[str, object]] = []
    aggregate: set[str] = set()
    projection_ids: set[str] = set()
    for narrative in narratives:
        consumed = tuple(getattr(narrative, "consumed_fact_ids", ()))
        digests = tuple(getattr(narrative, "consumed_fact_text_digests", ()))
        if not consumed and not digests:
            continue
        projection_id = getattr(narrative, "projection_id", "")
        source_id = getattr(narrative, "source_observation_event_id", "")
        evidence_id = getattr(narrative, "evidence_id", "")
        if (
            not _is_strict_lineage_id(projection_id)
            or not _is_strict_lineage_id(source_id)
            or not _is_strict_lineage_id(evidence_id)
            or len(consumed) != len(digests)
            or any(not _is_strict_lineage_id(item) for item in consumed)
            or any(
                not isinstance(item, str) or not item or item != item.strip() for item in digests
            )
            or len(set(consumed)) != len(consumed)
        ):
            raise ValueError("narrative consumed fact lineage is invalid")
        if projection_id in projection_ids or any(fact_id in aggregate for fact_id in consumed):
            raise ValueError("duplicate narrative lineage identifier")
        facts = [
            {
                "factId": fact_id,
                "factType": "visual_text",
                "textDigest": digest,
                "redaction": {"textPersisted": False},
            }
            for fact_id, digest in zip(consumed, digests, strict=True)
        ]
        if {str(item["factId"]) for item in facts} != set(consumed):
            raise ValueError("narrative consumed fact lineage is inconsistent")
        lineage.append(
            {
                "evidenceId": evidence_id,
                "projectionId": projection_id,
                "sourceObservationEventId": source_id,
                "consumedFactIds": list(consumed),
                "facts": facts,
            }
        )
        aggregate.update(consumed)
        projection_ids.add(projection_id)
    return lineage, sorted(aggregate)


def _is_strict_lineage_id(value: object) -> TypeGuard[str]:
    return type(value) is str and bool(value) and value == value.strip()


def _project_safe_completed_review_lineage(
    events: Sequence[AuditEvent],
    *,
    native_events: Sequence[OpenHandsEvent],
) -> dict[str, object]:
    """Project the product-owned completed-review lineage without content payloads."""

    from focusproof.media_projection.visual_fact_identity import (
        VISUAL_FACT_CAPABILITY,
        VISUAL_FACT_TOOL_NAME,
        derive_visual_fact_identities,
        derive_visual_projection_id,
    )

    score_events = [event for event in events if event.type == "score.calculated"]
    review_events = [event for event in events if event.type == "review.completed"]
    if len(score_events) != 1 or len(review_events) != 1:
        raise ValueError("completed review audit lineage must have one score and review event")
    score_event = score_events[0]
    review_event = review_events[0]
    raw_lineage = score_event.payload.get("narrativeLineage")
    consumed = score_event.payload.get("consumedFactIds")
    score_source = score_event.payload.get("sourceObservationEventId")
    review_score = review_event.payload.get("scoreEventId")
    review_projections = review_event.payload.get("narrativeProjectionIds")
    review_source = review_event.payload.get("sourceObservationEventId")
    last_answer_index = _last_answer_index(native_events)
    accepted_drafts = [
        event
        for index, event in enumerate(native_events)
        if index > last_answer_index
        and isinstance(event, ObservationEvent)
        and isinstance(event.observation, ReviewDraftObservation)
        and event.observation.accepted
    ]
    if not accepted_drafts:
        raise ValueError("completed review audit lineage has no official completion source")
    completion_source_id = str(accepted_drafts[-1].id)
    native_observations = {
        str(event.id): event for event in native_events if isinstance(event, ObservationEvent)
    }
    native_actions = {
        str(event.id): event for event in native_events if isinstance(event, ActionEvent)
    }
    if (
        not isinstance(raw_lineage, list)
        or not raw_lineage
        or not isinstance(consumed, list)
        or not isinstance(review_projections, list)
        or not _is_strict_lineage_id(score_source)
        or not _is_strict_lineage_id(review_source)
        or not _is_strict_lineage_id(review_score)
        or any(not _is_strict_lineage_id(item) for item in consumed)
        or any(not _is_strict_lineage_id(item) for item in review_projections)
        or len(set(consumed)) != len(consumed)
        or len(set(review_projections)) != len(review_projections)
        or score_source != completion_source_id
        or review_source != completion_source_id
        or score_event.id != f"evt_score_{completion_source_id}"
        or review_event.id != f"evt_review_{completion_source_id}"
    ):
        raise ValueError("completed review audit lineage is invalid")

    safe_lineage: list[dict[str, object]] = []
    fact_ids: list[str] = []
    projection_ids: list[str] = []
    source_ids: list[str] = []
    for item in raw_lineage:
        if not isinstance(item, dict):
            raise ValueError("completed review narrative lineage is invalid")
        projection_id = item.get("projectionId")
        source_id = item.get("sourceObservationEventId")
        evidence_id = item.get("evidenceId")
        lineage_consumed = item.get("consumedFactIds")
        facts = item.get("facts")
        if (
            not _is_strict_lineage_id(projection_id)
            or not _is_strict_lineage_id(source_id)
            or not _is_strict_lineage_id(evidence_id)
            or not isinstance(lineage_consumed, list)
            or not isinstance(facts, list)
            or any(not _is_strict_lineage_id(value) for value in lineage_consumed)
            or len(set(lineage_consumed)) != len(lineage_consumed)
        ):
            raise ValueError("completed review narrative lineage is invalid")
        native_source = native_observations.get(source_id)
        native_action = (
            native_actions.get(str(native_source.action_id)) if native_source is not None else None
        )
        if (
            native_source is None
            or not isinstance(native_source.observation, VerificationObservation)
            or native_source.observation.status != "success"
            or native_source.observation.capability != VISUAL_FACT_CAPABILITY
            or native_source.observation.evidence_id != evidence_id
            or evidence_id not in native_source.observation.source_refs
            or native_source.tool_name != VISUAL_FACT_TOOL_NAME
            or native_action is None
            or native_action.tool_name != native_source.tool_name
            or native_action.tool_call_id != native_source.tool_call_id
            or not isinstance(native_action.action, EvidenceReferenceAction)
            or native_action.action.evidence_id != evidence_id
        ):
            raise ValueError("completed review fact lineage is inconsistent")
        expected_facts = derive_visual_fact_identities(
            evidence_id,
            source_id,
            native_source.observation.facts.get("visual_facts"),
        )
        if not expected_facts or len(facts) != len(expected_facts):
            raise ValueError("completed review fact lineage is inconsistent")

        safe_facts: list[dict[str, str]] = []
        for fact, expected_fact in zip(facts, expected_facts, strict=True):
            if not isinstance(fact, dict):
                raise ValueError("completed review fact lineage is invalid")
            fact_id = fact.get("factId")
            fact_type = fact.get("factType")
            text_digest = fact.get("textDigest")
            if (
                set(fact) != {"factId", "factType", "textDigest", "redaction"}
                or fact_id != expected_fact.fact_id
                or fact_type != expected_fact.fact_type
                or text_digest != expected_fact.text_digest
                or fact.get("redaction") != {"textPersisted": False}
            ):
                raise ValueError("completed review fact lineage is invalid")
            safe_facts.append(
                {"factId": expected_fact.fact_id, "factType": expected_fact.fact_type}
            )
        lineage_fact_ids = [fact["factId"] for fact in safe_facts]
        expected_projection_id = derive_visual_projection_id(
            evidence_id,
            source_id,
            lineage_fact_ids,
        )
        if lineage_fact_ids != lineage_consumed or projection_id != expected_projection_id:
            raise ValueError("completed review fact lineage is inconsistent")
        if (
            projection_id in projection_ids
            or source_id in source_ids
            or any(fact_id in fact_ids for fact_id in lineage_fact_ids)
        ):
            raise ValueError("completed review audit lineage contains duplicate identifiers")
        safe_lineage.append(
            {
                "projectionId": projection_id,
                "sourceObservationEventId": source_id,
                "facts": safe_facts,
                "consumedFactIds": list(lineage_consumed),
                "factCount": len(lineage_fact_ids),
            }
        )
        projection_ids.append(projection_id)
        source_ids.append(source_id)
        fact_ids.extend(lineage_fact_ids)

    if (
        consumed != sorted(fact_ids)
        or review_score != score_event.id
        or review_projections != projection_ids
    ):
        raise ValueError("completed review audit lineage is inconsistent")
    return {
        "projection": projection_ids[0],
        "projectionIds": projection_ids,
        "source": source_ids[0],
        "sourceObservationEventIds": source_ids,
        "fact": fact_ids[0],
        "factIds": fact_ids,
        "consumedFactIds": list(consumed),
        "consumedFactCount": len(consumed),
        "score": score_event.id,
        "review": review_event.id,
        "review_score": review_score,
        "review_projection": review_projections[0],
        "reviewProjectionIds": list(review_projections),
        "lineage": safe_lineage,
        "auditEvents": [
            {"eventType": score_event.type, "id": score_event.id},
            {"eventType": review_event.type, "id": review_event.id},
        ],
    }


def _last_answer_index(native_events: Sequence[OpenHandsEvent]) -> int:
    latest = -1
    for index, event in enumerate(native_events):
        if isinstance(event, MessageEvent) and _message_kind(event) == "answer":
            latest = index
    return latest


def _last_accepted_draft_index(
    native_events: Sequence[OpenHandsEvent],
    *,
    before_index: int | None = None,
) -> int:
    latest = -1
    for index, event in enumerate(native_events):
        if before_index is not None and index >= before_index:
            break
        if (
            isinstance(event, ObservationEvent)
            and isinstance(event.observation, ReviewDraftObservation)
            and event.observation.accepted
        ):
            latest = index
    return latest


def _message_kind(event: MessageEvent) -> str | None:
    text = "".join(item.text for item in event.llm_message.content if isinstance(item, TextContent))
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
    before_index: int | None = None,
) -> list[Observation]:
    observations: list[Observation] = []
    for index, event in enumerate(native_events):
        if before_index is not None and index >= before_index:
            break
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
                sourceEventId=str(event.id),
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
