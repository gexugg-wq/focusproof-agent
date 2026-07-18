import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction

from focusproof.openhands_runtime.tools.verification import (
    EvidenceReferenceAction,
    VerificationObservation,
)
from focusproof.openhands_runtime.tools.evidence_verification import (
    EvidenceVerificationAction,
    EvidenceVerificationObservation,
)
from focusproof.runtime.audit_projection import InMemoryAuditProjectionStore
from focusproof.runtime.events import Actor, Event, EventType


def _native_action() -> ActionEvent:
    tool_call = MessageToolCall(
        id="call_ev_1",
        name="focusproof_text_evidence_verification",
        arguments='{"evidence_id":"ev_1"}',
        origin="completion",
    )
    return ActionEvent(
        thought=[TextContent(text="Verify authoritative evidence")],
        action=EvidenceReferenceAction(evidence_id="ev_1"),
        tool_name="focusproof_text_evidence_verification",
        tool_call_id=tool_call.id,
        tool_call=tool_call,
        llm_response_id="response_1",
    )


def _native_observation(action: ActionEvent) -> ObservationEvent:
    now = datetime.now(UTC)
    observation = VerificationObservation.from_text(
        "verified",
        evidence_id="ev_1",
        capability="text",
        status="success",
        facts={"has_text": True},
        weak_signals=[],
        source_refs=["ev_1"],
        verifier_version="1",
        started_at=now,
        completed_at=now,
    )
    return ObservationEvent(
        tool_name=action.tool_name,
        tool_call_id=action.tool_call_id,
        observation=observation,
        action_id=action.id,
    )


def test_message_projection_preserves_native_identity() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    log = InMemoryAuditProjectionStore()
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


def test_legacy_message_projection_redacts_url_and_metadata_secrets() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    log = InMemoryAuditProjectionStore()
    projector = OpenHandsEventProjector("sess_1", uuid4(), log)
    envelope = {
        "kind": "evidence",
        "session_id": "sess_1",
        "evidence": {
            "evidenceId": "ev_url_secret",
            "evidenceType": "url",
            "contentHash": "sha256:url-secret",
            "sourceUrl": (
                "https://credential-user:credential-password@example.com:8443/private/secret-token"
                "?token=query-secret#fragment-secret"
            ),
            "metadata": {"callback": "https://example.com/metadata-secret"},
        },
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
    serialized = json.dumps(projected.payload, sort_keys=True)
    assert set(projected.payload) >= {
        "evidenceId",
        "evidenceType",
        "contentHash",
        "source",
    }
    for secret in (
        "credential-user",
        "credential-password",
        "private",
        "secret-token",
        "query-secret",
        "fragment-secret",
        "metadata-secret",
    ):
        assert secret not in serialized


def test_current_message_projection_preserves_only_safe_url_diagnostics() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    log = InMemoryAuditProjectionStore()
    projector = OpenHandsEventProjector("sess_1", uuid4(), log)
    digest = "a" * 64
    envelope = {
        "schema_version": 1,
        "message_key": "evidence:ev_url",
        "kind": "evidence",
        "session_id": "sess_1",
        "payload": {
            "evidenceId": "ev_url",
            "evidenceType": "url",
            "contentHash": "sha256:url",
            "source": {
                "scheme": "https",
                "hostname": "example.com",
                "port": 8443,
                "origin": "https://example.com:8443",
                "path_redacted": True,
                "url_sha256": digest,
                "unexpected": "must-be-dropped",
            },
        },
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
    assert projected.payload["source"] == {
        "scheme": "https",
        "hostname": "example.com",
        "port": 8443,
        "origin": "https://example.com:8443",
        "path_redacted": True,
        "url_sha256": digest,
    }


def test_preclosure_url_observation_projection_redacts_legacy_facts_read_only() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    now = datetime.now(UTC)
    observation = VerificationObservation.from_text(
        "legacy URL facts",
        evidence_id="ev_old_url",
        capability="url",
        status="success",
        facts={
            "normalized_url": "https://example.com/private/path-secret",
            "hostname": "example.com",
            "status_code": 200,
            "content_type": "text/plain",
            "content_length": 10,
            "redirect_chain": [
                "https://redirect.example/signed/redirect-secret"
            ],
            "title": "path-secret",
            "text_excerpt": "redirect-secret",
        },
        weak_signals=[],
        source_refs=[
            "ev_old_url",
            "https://example.com/private/path-secret",
        ],
        verifier_version="1",
        started_at=now,
        completed_at=now,
    )
    native = ObservationEvent(
        tool_name="focusproof_url_evidence_verification",
        tool_call_id="call_old_url",
        observation=observation,
        action_id="action_old_url",
    )
    before = native.model_dump_json()
    projector = OpenHandsEventProjector(
        "sess_1", uuid4(), InMemoryAuditProjectionStore()
    )

    projected = projector.on_event(native)

    assert projected is not None
    assert native.model_dump_json() == before
    assert projected.payload["facts"]["url"]["origin"] == "https://example.com"
    serialized = json.dumps(projected.payload, sort_keys=True)
    assert "path-secret" not in serialized
    assert "redirect-secret" not in serialized


def test_action_and_observation_projection_preserves_order_and_tool_call_id() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    log = InMemoryAuditProjectionStore()
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

    log = InMemoryAuditProjectionStore()
    projector = OpenHandsEventProjector("sess_1", uuid4(), log)
    action = _native_action()

    projector.on_event(action)
    first_count = log.count("sess_1")

    assert projector.reconcile([action]) == 0
    assert log.count("sess_1") == first_count


def test_legacy_verification_events_are_projected_read_only_and_idempotently() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    log = InMemoryAuditProjectionStore()
    projector = OpenHandsEventProjector("sess_1", uuid4(), log)
    tool_call = MessageToolCall(
        id="call_legacy_1",
        name="focusproof_evidence_verification",
        arguments='{"evidence_id":"ev_legacy"}',
        origin="completion",
    )
    action = ActionEvent(
        thought=[TextContent(text="Read the legacy result")],
        action=EvidenceVerificationAction(evidence_id="ev_legacy"),
        tool_name="focusproof_evidence_verification",
        tool_call_id=tool_call.id,
        tool_call=tool_call,
        llm_response_id="response_legacy_1",
    )
    observation = ObservationEvent(
        tool_name=action.tool_name,
        tool_call_id=action.tool_call_id,
        observation=EvidenceVerificationObservation.from_text(
            "legacy result",
            evidence_id="ev_legacy",
            verified=True,
            evidence_type="text",
            findings=["Legacy verifier found repository content."],
            weak_signals=["Legacy verified is not a learning verdict."],
            source_refs=["ev_legacy", "sha256:legacy"],
            verifier="focusproof-session-repository",
        ),
        action_id=action.id,
    )
    before = [action.model_dump_json(), observation.model_dump_json()]

    assert projector.reconcile([action, observation]) == 2
    assert projector.reconcile([action, observation]) == 0
    assert [action.model_dump_json(), observation.model_dump_json()] == before
    projected = log.list("sess_1")
    assert [event.type for event in projected] == [
        "verification.requested",
        "verification.completed",
    ]
    assert projected[1].payload["status"] == "inconclusive"
    assert projected[1].payload["weak_signals"] == [
        "Legacy verified is not a learning verdict."
    ]
    assert projected[1].payload["source_refs"] == [
        "ev_legacy",
        "sha256:legacy",
    ]
    assert "verified" not in projected[1].payload


def test_finish_action_is_recognized_as_native_terminal_event() -> None:
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    log = InMemoryAuditProjectionStore()
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

    class FlakyAuditProjectionStore(InMemoryAuditProjectionStore):
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

    log = FlakyAuditProjectionStore()
    projector = OpenHandsEventProjector("sess_1", uuid4(), log)
    action = _native_action()
    with pytest.raises(RuntimeError, match="audit commit failed"):
        projector.on_event(action)

    assert projector.reconcile([action]) == 1
    assert projector.reconcile([action]) == 0
    assert log.count("sess_1") == 1
