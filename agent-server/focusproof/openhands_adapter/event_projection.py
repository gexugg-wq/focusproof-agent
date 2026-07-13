from __future__ import annotations

from typing import Any

from focusproof.runtime.actions import Action
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.events import Event
from focusproof.runtime.observations import Observation

RuntimeSource = str


def _metadata(
    *,
    session_id: str,
    runtime_source: RuntimeSource,
    openhands_event_kind: str,
    source_index: int,
    related_evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "sessionId": session_id,
        "runtimeSource": runtime_source,
        "sourceRuntime": runtime_source,
        "openhandsEventKind": openhands_event_kind,
        "sourceIndex": source_index,
        "relatedEvidenceIds": related_evidence_ids or [],
    }


def project_user_goal_to_message_event(
    *,
    session_id: str,
    goal: LearningGoal,
    runtime_source: RuntimeSource,
    source_index: int,
) -> Event:
    payload = goal.model_dump()
    payload.update(
        _metadata(
            session_id=session_id,
            runtime_source=runtime_source,
            openhands_event_kind="MessageEvent",
            source_index=source_index,
        )
    )
    return Event(sessionId=session_id, type="goal.submitted", sequence=0, actor="user", payload=payload)


def project_evidence_to_message_event(
    *,
    session_id: str,
    evidence: Evidence,
    runtime_source: RuntimeSource,
    source_index: int,
) -> Event:
    payload = evidence.model_dump()
    payload.update(
        _metadata(
            session_id=session_id,
            runtime_source=runtime_source,
            openhands_event_kind="MessageEvent",
            source_index=source_index,
            related_evidence_ids=[evidence.evidenceId],
        )
    )
    return Event(sessionId=session_id, type="evidence.submitted", sequence=0, actor="user", payload=payload)


def project_answer_to_message_event(
    *,
    session_id: str,
    question_id: str,
    answer: str,
    runtime_source: RuntimeSource,
    source_index: int,
) -> Event:
    payload = {"questionId": question_id, "answer": answer}
    payload.update(
        _metadata(
            session_id=session_id,
            runtime_source=runtime_source,
            openhands_event_kind="MessageEvent",
            source_index=source_index,
        )
    )
    return Event(sessionId=session_id, type="answer.submitted", sequence=0, actor="user", payload=payload)


def project_action_to_focusproof_event(
    *,
    session_id: str,
    action: Action,
    runtime_source: RuntimeSource,
    source_index: int,
) -> Event:
    related = action.evidenceIds or action.relatedEvidenceIds
    payload = action.model_dump()
    payload.update(
        _metadata(
            session_id=session_id,
            runtime_source=runtime_source,
            openhands_event_kind="ActionEvent",
            source_index=source_index,
            related_evidence_ids=related,
        )
    )
    if action.type == "ask_question":
        payload.setdefault("questionId", f"q_{source_index}")
        return Event(sessionId=session_id, type="question.asked", sequence=0, actor="agent", payload=payload)
    return Event(sessionId=session_id, type="verification.requested", sequence=0, actor="agent", payload=payload)


def project_observation_to_focusproof_event(
    *,
    session_id: str,
    observation: Observation,
    runtime_source: RuntimeSource,
    source_index: int,
) -> Event:
    payload = observation.model_dump()
    payload.update(
        _metadata(
            session_id=session_id,
            runtime_source=runtime_source,
            openhands_event_kind="ObservationEvent",
            source_index=source_index,
            related_evidence_ids=observation.sourceRefs,
        )
    )
    return Event(sessionId=session_id, type="verification.completed", sequence=0, actor="tool", payload=payload)


def project_openhands_output_to_review_input(raw_output: str | None) -> dict[str, Any]:
    return {"rawOpenHandsOutput": raw_output, "projection": "review-input"}
