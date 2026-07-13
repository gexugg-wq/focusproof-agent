from focusproof.openhands_adapter.event_projection import (
    project_action_to_focusproof_event,
    project_answer_to_message_event,
    project_evidence_to_message_event,
    project_observation_to_focusproof_event,
    project_user_goal_to_message_event,
)
from focusproof.runtime.actions import Action
from focusproof.runtime.evidence import Evidence, LearningGoal
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
            toolName="FakeTextEvidenceTool",
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
            toolName="FakeTextEvidenceTool",
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
