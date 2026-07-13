from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.base import Event as OpenHandsEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import TextContent
from openhands.sdk.tool.builtins.finish import FinishAction

from focusproof.openhands_runtime.tools.evidence_verification import (
    EvidenceVerificationAction,
    EvidenceVerificationObservation,
)
from focusproof.openhands_runtime.tools.learner_input import (
    LearnerInputAction,
    question_id_for,
)
from focusproof.runtime.events import Actor, Event, EventType


class AuditProjection(Protocol):
    def append(
        self,
        session_id: str,
        event_type: EventType,
        actor: Actor,
        payload: dict[str, object],
    ) -> Event: ...

    def has_source_event(self, session_id: str, source_event_id: str) -> bool: ...


class OpenHandsEventProjector:
    def __init__(
        self,
        session_id: str,
        conversation_id: UUID,
        audit_log: AuditProjection,
    ) -> None:
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.audit_log = audit_log
        self._callback_index = 0

    def on_event(self, native_event: OpenHandsEvent) -> Event | None:
        index = self._callback_index
        self._callback_index += 1
        return self._project(native_event, index)

    def reconcile(self, native_events: Sequence[OpenHandsEvent]) -> int:
        projected = 0
        for index, native_event in enumerate(native_events):
            if self._project(native_event, index) is not None:
                projected += 1
        self._callback_index = max(self._callback_index, len(native_events))
        return projected

    def _project(self, native_event: OpenHandsEvent, source_index: int) -> Event | None:
        if self.audit_log.has_source_event(self.session_id, native_event.id):
            return None
        if isinstance(native_event, MessageEvent):
            return self._project_message(native_event, source_index)
        if isinstance(native_event, ActionEvent):
            return self._project_action(native_event, source_index)
        if isinstance(native_event, ObservationEvent):
            return self._project_observation(native_event, source_index)
        if isinstance(native_event, ConversationErrorEvent):
            payload: dict[str, Any] = {
                "code": native_event.code,
                "detail": native_event.detail,
            }
            payload.update(self._metadata(native_event, source_index))
            return self.audit_log.append(
                self.session_id,
                "error.occurred",
                "system",
                payload,
            )
        return None

    def _project_message(self, native_event: MessageEvent, source_index: int) -> Event | None:
        if native_event.source != "user":
            return None
        envelope = _message_envelope(native_event)
        if envelope is None or envelope.get("session_id") != self.session_id:
            return None
        kind = envelope.get("kind")
        product_payload = envelope.get("payload")
        if not isinstance(product_payload, dict):
            product_payload = None
        if kind == "goal" and (
            product_payload is not None or isinstance(envelope.get("goal"), dict)
        ):
            payload = dict(product_payload or envelope["goal"])
            event_type: EventType = "goal.submitted"
            related: list[str] = []
        elif kind == "evidence" and (
            product_payload is not None
            or isinstance(envelope.get("evidence"), dict)
        ):
            payload = dict(product_payload or envelope["evidence"])
            event_type = "evidence.submitted"
            evidence_id = payload.get("evidenceId")
            related = [evidence_id] if isinstance(evidence_id, str) else []
        elif kind == "answer":
            answer_payload = product_payload or envelope
            question_id = answer_payload.get("questionId", envelope.get("question_id"))
            answer = answer_payload.get("answer")
            if not isinstance(question_id, str) or not isinstance(answer, str):
                return None
            payload = {"questionId": question_id, "answer": answer}
            event_type = "answer.submitted"
            related = []
        else:
            return None
        payload.update(self._metadata(native_event, source_index, related_evidence_ids=related))
        return self.audit_log.append(self.session_id, event_type, "user", payload)

    def _project_action(self, native_event: ActionEvent, source_index: int) -> Event | None:
        action = native_event.action
        if isinstance(action, EvidenceVerificationAction):
            payload = action.model_dump(mode="json")
            event_type: EventType = "verification.requested"
            related = [action.evidence_id]
        elif isinstance(action, LearnerInputAction):
            payload = action.model_dump(mode="json")
            payload["questionId"] = question_id_for(action)
            event_type = "question.asked"
            related = []
        elif isinstance(action, FinishAction):
            payload = action.model_dump(mode="json")
            event_type = "session.ended"
            related = []
        else:
            return None
        payload["toolName"] = native_event.tool_name
        payload.update(
            self._metadata(
                native_event,
                source_index,
                tool_call_id=native_event.tool_call_id,
                related_evidence_ids=related,
            )
        )
        return self.audit_log.append(self.session_id, event_type, "agent", payload)

    def _project_observation(
        self,
        native_event: ObservationEvent,
        source_index: int,
    ) -> Event | None:
        observation = native_event.observation
        if isinstance(observation, EvidenceVerificationObservation):
            payload = observation.model_dump(mode="json")
            event_type: EventType = "verification.completed"
            actor: Actor = "tool"
            related = [observation.evidence_id]
        else:
            return None
        payload.update(
            self._metadata(
                native_event,
                source_index,
                tool_call_id=native_event.tool_call_id,
                related_evidence_ids=related,
            )
        )
        return self.audit_log.append(
            self.session_id,
            event_type,
            actor,
            payload,
        )

    def _metadata(
        self,
        native_event: OpenHandsEvent,
        source_index: int,
        *,
        tool_call_id: str | None = None,
        related_evidence_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sourceRuntime": "openhands-local",
            "runtimeSource": "openhands-local",
            "sourceConversationId": str(self.conversation_id),
            "sourceOpenHandsEventId": native_event.id,
            "sourceOpenHandsEventType": type(native_event).__name__,
            "sourceOpenHandsEventIndex": source_index,
            "relatedEvidenceIds": related_evidence_ids or [],
            "sessionId": self.session_id,
        }
        if tool_call_id is not None:
            payload["sourceToolCallId"] = tool_call_id
        return payload


def _message_envelope(native_event: MessageEvent) -> dict[str, Any] | None:
    text = "".join(
        item.text
        for item in native_event.llm_message.content
        if isinstance(item, TextContent)
    )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
