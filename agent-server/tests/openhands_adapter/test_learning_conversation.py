from focusproof.openhands_adapter.learning_conversation import FocusProofLearningConversation
from focusproof.runtime.evidence import Evidence, LearningGoal


def _goal() -> LearningGoal:
    return LearningGoal(domain="general", title="Learn event logs", goal="Explain event replay")


def _evidence() -> Evidence:
    return Evidence(
        evidenceId="ev_1",
        evidenceType="text",
        contentHash="sha256:e",
        textContent="Events are appended and replayed into a view with immutable facts.",
    )


def test_create_returns_truthful_projection_fallback_by_default() -> None:
    conversation = FocusProofLearningConversation.create("sess_1", _goal())

    assert conversation.session_id == "sess_1"
    assert conversation.conversation_mode == "projection-fallback"
    assert conversation.used_openhands_conversation is False


def test_submit_evidence_creates_message_event_projection() -> None:
    conversation = FocusProofLearningConversation.create("sess_1", _goal())
    events = conversation.submit_evidence(_evidence())

    assert events[0].type == "evidence.submitted"
    assert events[0].payload["openhandsEventKind"] == "MessageEvent"
    assert events[0].payload["runtimeSource"] == "projection-fallback"


def test_run_review_creates_action_observation_and_review_result() -> None:
    conversation = FocusProofLearningConversation.create("sess_1", _goal())
    result = conversation.run_review(evidence=[_evidence()], answers=[])
    event_types = [event.type for event in result.focusproofEvents]

    assert result.conversationMode == "projection-fallback"
    assert result.usedOpenHandsConversation is False
    assert result.actionEventsCount >= 1
    assert result.observationEventsCount >= 1
    assert "verification.requested" in event_types
    assert "verification.completed" in event_types
    assert "score.calculated" in event_types
    assert "review.completed" in event_types
    assert result.reviewResult.score >= 0
    assert result.unsafeToolsBlocked
