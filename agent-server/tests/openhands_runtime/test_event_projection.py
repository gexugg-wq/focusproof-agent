import json
from uuid import uuid4

import pytest
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction

from focusproof.openhands_runtime.tools.evidence_verification import (
    EvidenceVerificationAction,
    EvidenceVerificationObservation,
)
from focusproof.runtime.event_log import InMemoryEventLog
from focusproof.runtime.events import Actor, Event, EventType


def _native_action() -> ActionEvent:
    tool_call = MessageToolCall(
        id="call_ev_1",
        name="focusproof_evidence_verification",
        arguments='{"evidence_id":"ev_1"}',
        origin="completion",
    )
    return ActionEvent(
        thought=[TextContent(text="Verify authoritative evidence")],
        action=EvidenceVerificationAction(evidence_id="ev_1"),
        tool_name="focusproof_evidence_verification",
        tool_call_id=tool_call.id,
        tool_call=tool_call,
        llm_response_id="response_1",
    )


def _native_observation(action: ActionEvent) -> ObservationEvent:
    observation = EvidenceVerificationObservation.from_text(
        "verified",
        evidence_id="ev_1",
        verified=True,
        evidence_type="text",
        findings=["specific"],
        weak_signals=[],
        source_refs=["ev_1"],
        verifier="test",
    )
    return ObservationEvent(
        tool_name=action.tool_name,
        tool_call_id=action.tool_call_id,
        observation=observation,
        action_id=action.id,
    )


def test_message_projection_preserves_native_identity() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    log = InMemoryEventLog()
    conversation_id = uuid4()
    projector = OpenHandsEventProjector("sess_1", conversation_id, log)
    envelope = {
        "kind": "evidence",
        "session_id": "sess_1",
        "evidence": {"evidenceId": "ev_1", "evidenceType": "text"},
    }
    native = MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text=json.dumps(envelope))],
        ),
    )

    projected = projector.on_event(native)

    assert projected is not None
    assert projected.type == "evidence.submitted"
    assert projected.payload["sourceRuntime"] == "openhands-local"
    assert projected.payload["sourceConversationId"] == str(conversation_id)
    assert projected.payload["sourceOpenHandsEventId"] == native.id
    assert projected.payload["sourceOpenHandsEventType"] == "MessageEvent"
    assert projected.payload["relatedEvidenceIds"] == ["ev_1"]


def test_action_and_observation_projection_preserves_order_and_tool_call_id() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    log = InMemoryEventLog()
    projector = OpenHandsEventProjector("sess_1", uuid4(), log)
    action = _native_action()
    observation = _native_observation(action)

    assert projector.reconcile([action, observation]) == 2

    events = log.list("sess_1")
    assert [event.type for event in events] == [
        "verification.requested",
        "verification.completed",
    ]
    assert events[0].payload["sourceToolCallId"] == action.tool_call_id
    assert events[1].payload["sourceToolCallId"] == observation.tool_call_id
    assert events[0].payload["sourceOpenHandsEventIndex"] == 0
    assert events[1].payload["sourceOpenHandsEventIndex"] == 1


def test_reconcile_does_not_duplicate_projected_native_event() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    log = InMemoryEventLog()
    projector = OpenHandsEventProjector("sess_1", uuid4(), log)
    action = _native_action()

    projector.on_event(action)
    first_count = log.count("sess_1")

    assert projector.reconcile([action]) == 0
    assert log.count("sess_1") == first_count


def test_finish_action_is_recognized_as_native_terminal_event() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    log = InMemoryEventLog()
    projector = OpenHandsEventProjector("sess_1", uuid4(), log)
    tool_call = MessageToolCall(
        id="call_finish_1",
        name="finish",
        arguments='{"message":"done"}',
        origin="completion",
    )
    native = ActionEvent(
        thought=[TextContent(text="Finish")],
        action=FinishAction(message="done"),
        tool_name="finish",
        tool_call_id=tool_call.id,
        tool_call=tool_call,
        llm_response_id="response_finish",
    )

    projected = projector.on_event(native)

    assert projected is not None
    assert projected.type == "session.ended"
    assert projected.payload["sourceOpenHandsEventType"] == "ActionEvent"
    assert projected.payload["sourceToolCallId"] == "call_finish_1"


def test_reconcile_recovers_after_projection_commit_failure() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    class FlakyAuditLog(InMemoryEventLog):
        def __init__(self) -> None:
            super().__init__()
            self.fail_once = True

        def append(
            self,
            session_id: str,
            event_type: EventType,
            actor: Actor,
            payload: dict[str, object],
        ) -> Event:
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("audit commit failed")
            return super().append(session_id, event_type, actor, payload)

    log = FlakyAuditLog()
    projector = OpenHandsEventProjector("sess_1", uuid4(), log)
    action = _native_action()
    with pytest.raises(RuntimeError, match="audit commit failed"):
        projector.on_event(action)

    assert projector.reconcile([action]) == 1
    assert projector.reconcile([action]) == 0
    assert log.count("sess_1") == 1
