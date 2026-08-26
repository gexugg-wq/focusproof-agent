import pytest
from openhands.sdk.event import MessageEvent

from focusproof.openhands_adapter.event_projection import (
    project_action_to_focusproof_event,
    project_answer_to_message_event,
    project_evidence_to_message_event,
    project_observation_to_focusproof_event,
    project_user_goal_to_message_event,
)
from focusproof.openhands_adapter.events import (
    focusproof_event_to_openhands_message,
    openhands_message_to_focusproof_payload,
)
from focusproof.runtime.actions import Action
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.events import Event
from focusproof.runtime.observations import Observation


def test_goal_projects_to_message_event_with_runtime_metadata() -> None:
    event = project_user_goal_to_message_event(
        session_id="sess_1",
        goal=LearningGoal(domain="general", title="T", goal="Learn events"),
        runtime_source="projection-fallback",
        source_index=1,
    )

    assert event.type == "goal.submitted"
    assert event.payload["runtimeSource"] == "projection-fallback"
    assert event.payload["openhandsEventKind"] == "MessageEvent"
    assert event.payload["sourceIndex"] == 1
    assert event.payload["sessionId"] == "sess_1"


def test_evidence_and_answer_project_to_message_events() -> None:
    evidence_event = project_evidence_to_message_event(
        session_id="sess_1",
        evidence=Evidence(
            evidenceId="ev_1",
            evidenceType="text",
            contentHash="sha256:x",
            textContent="Concrete event replay notes",
        ),
        runtime_source="projection-fallback",
        source_index=2,
    )
    answer_event = project_answer_to_message_event(
        session_id="sess_1",
        question_id="q_1",
        answer="Replay rebuilds the current view from immutable facts.",
        runtime_source="projection-fallback",
        source_index=3,
    )

    assert evidence_event.type == "evidence.submitted"
    assert evidence_event.payload["openhandsEventKind"] == "MessageEvent"
    assert evidence_event.payload["relatedEvidenceIds"] == ["ev_1"]
    assert answer_event.type == "answer.submitted"
    assert answer_event.payload["openhandsEventKind"] == "MessageEvent"


def test_action_projects_to_verification_or_question_event() -> None:
    verify_event = project_action_to_focusproof_event(
        session_id="sess_1",
        action=Action(
            type="verify_evidence",
            toolName="focusproof_text_evidence_verification",
            input={"text": "event replay"},
            evidenceIds=["ev_1"],
        ),
        runtime_source="projection-fallback",
        source_index=4,
    )
    question_event = project_action_to_focusproof_event(
        session_id="sess_1",
        action=Action(
            type="ask_question",
            question="What changed in your understanding?",
            reason="Need explanation",
            relatedEvidenceIds=["ev_1"],
        ),
        runtime_source="projection-fallback",
        source_index=5,
    )

    assert verify_event.type == "verification.requested"
    assert verify_event.payload["openhandsEventKind"] == "ActionEvent"
    assert verify_event.payload["relatedEvidenceIds"] == ["ev_1"]
    assert question_event.type == "question.asked"
    assert question_event.payload["openhandsEventKind"] == "ActionEvent"


def test_observation_projects_to_verification_completed() -> None:
    event = project_observation_to_focusproof_event(
        session_id="sess_1",
        observation=Observation(
            toolName="focusproof_text_evidence_verification",
            status="success",
            facts={"isSpecific": True},
            sourceRefs=["ev_1"],
        ),
        runtime_source="projection-fallback",
        source_index=6,
    )

    assert event.type == "verification.completed"
    assert event.payload["openhandsEventKind"] == "ObservationEvent"
    assert event.payload["runtimeSource"] == "projection-fallback"
    assert event.payload["relatedEvidenceIds"] == ["ev_1"]


def test_focusproof_event_adapter_emits_official_sdk_message_event() -> None:
    product_event = Event(
        id="evt_goal",
        sessionId="sess_1",
        type="goal.submitted",
        sequence=7,
        actor="user",
        payload={"goal": "Use official event projection", "sender": "payload-forged"},
    )

    event = focusproof_event_to_openhands_message(product_event, verified_sender="verified-user-1")

    assert isinstance(event, MessageEvent)
    assert event.__class__.__module__.startswith("openhands.sdk.event.")
    assert event.source == "user"
    assert event.sender == "verified-user-1"
    assert "payload-forged" not in event.model_dump_json()


@pytest.mark.parametrize("actor", ["user", "agent", "tool", "system"])
def test_verified_sender_is_independent_from_event_source(actor: str) -> None:
    product_event = Event(
        id=f"evt_{actor}",
        sessionId="sess_1",
        type="goal.submitted",
        sequence=9,
        actor=actor,
        payload={"goal": "Stable provenance"},
    )

    event = focusproof_event_to_openhands_message(product_event, verified_sender="verified-user-1")

    assert event.source == ("user" if actor == "user" else "environment")
    assert event.sender == "verified-user-1"
    assert f"focusproof-event:evt_{actor}" in event.llm_message.content[0].text


def test_missing_or_empty_verified_sender_fails_closed() -> None:
    product_event = Event(
        id="evt_missing_sender",
        sessionId="sess_1",
        type="goal.submitted",
        sequence=10,
        actor="user",
        payload={"goal": "Require provenance"},
    )

    with pytest.raises(TypeError):
        focusproof_event_to_openhands_message(product_event)
    with pytest.raises(ValueError, match="verified_sender"):
        focusproof_event_to_openhands_message(product_event, verified_sender=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="verified_sender"):
        focusproof_event_to_openhands_message(product_event, verified_sender="   ")


def test_focusproof_event_adapter_uses_kind_specific_safe_field_allowlist() -> None:
    product_event = Event(
        id="evt_evidence_safe_fields",
        sessionId="sess_1",
        type="evidence.submitted",
        sequence=8,
        actor="user",
        payload={
            "evidenceId": "ev_1",
            "evidenceType": "text",
            "contentHash": "sha256:safe",
            "textContent": "A safe explanation.",
            "opaque_object_key": "private-object-key",
            "temporary_path": "/private/tmp/focusproof-secret",
            "credential": "credential-value",
            "api_token": "token-value",
            "private_metadata": {"internal": "private-value"},
            "sender": "payload-forged",
            "raw_bytes": b"not-agent-visible",
        },
    )

    projected = focusproof_event_to_openhands_message(
        product_event, verified_sender="verified-user-1"
    )
    payload = openhands_message_to_focusproof_payload(projected)
    serialized = projected.model_dump_json()

    assert projected.sender == "verified-user-1"
    assert payload["evidenceId"] == "ev_1"
    assert payload["textContent"] == "A safe explanation."
    for forbidden in (
        "private-object-key",
        "/private/tmp/focusproof-secret",
        "credential-value",
        "token-value",
        "private-value",
        "payload-forged",
        "not-agent-visible",
    ):
        assert forbidden not in serialized


def test_openhands_adapter_rejects_raw_dict_payloads() -> None:
    with pytest.raises(TypeError, match="MessageEvent"):
        openhands_message_to_focusproof_payload({"raw": "not official"})
