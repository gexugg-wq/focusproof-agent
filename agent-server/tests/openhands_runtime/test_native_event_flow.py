from pathlib import Path

from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent

from focusproof.runtime.evidence import Evidence, LearningGoal

from .conftest import SessionRepository, completed_review_llm


def test_manager_run_uses_native_action_tool_and_observation_flow(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
    evidence: Evidence,
) -> None:
    from focusproof.openhands_runtime.manager import ConversationManager
    from focusproof.runtime.event_log import InMemoryEventLog

    audit_log = InMemoryEventLog()
    manager = ConversationManager(
        repository=repository,
        audit_log=audit_log,
        project_root=tmp_path,
        llm_factory=completed_review_llm,
    )
    handle = manager.create("sess_native", learning_goal)
    repository.add_evidence("sess_native", evidence)
    manager.send_evidence("sess_native", evidence)

    result = manager.run_review("sess_native")
    native_events = list(handle.conversation.state.events)
    verification_action = next(
        event
        for event in native_events
        if isinstance(event, ActionEvent)
        and event.tool_name == "focusproof_evidence_verification"
    )
    verification_observation = next(
        event
        for event in native_events
        if isinstance(event, ObservationEvent)
        and event.tool_name == "focusproof_evidence_verification"
    )

    assert result.conversationMode == "openhands-local-scripted-test"
    assert result.usedOpenHandsConversation is True
    assert result.reviewStatus == "completed"
    assert result.reviewResult is not None
    assert any(isinstance(event, MessageEvent) for event in native_events)
    assert verification_action.tool_call_id == verification_observation.tool_call_id
    assert native_events.index(verification_action) < native_events.index(
        verification_observation
    )
    assert audit_log.get_by_type("sess_native", "verification.requested")
    assert audit_log.get_by_type("sess_native", "verification.completed")
    manager.close("sess_native")
