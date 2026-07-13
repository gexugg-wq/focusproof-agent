from pathlib import Path

import pytest
from openhands.sdk.testing import TestLLM

from focusproof.runtime.evidence import Evidence, LearningGoal

from .conftest import SessionRepository, awaiting_user_llm, completed_review_llm


def test_manager_reuses_same_conversation(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
    evidence: Evidence,
) -> None:
    from focusproof.openhands_runtime.manager import ConversationManager
    from focusproof.runtime.event_log import InMemoryEventLog

    manager = ConversationManager(
        repository=repository,
        audit_log=InMemoryEventLog(),
        project_root=tmp_path,
        llm_factory=lambda session_id: TestLLM.from_messages([]),
    )
    first = manager.create("sess_1", learning_goal)
    repository.add_evidence("sess_1", evidence)
    manager.send_evidence("sess_1", evidence)
    second = manager.get("sess_1")

    assert second.conversation is first.conversation
    assert second.conversation_id == first.conversation_id
    assert first.workspace_path == tmp_path / "var/conversations/sess_1/workspace"
    assert first.persistence_path == tmp_path / "var/conversations/sess_1/persistence"
    manager.close("sess_1")


def test_learner_input_stops_before_scoring(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
) -> None:
    from focusproof.openhands_runtime.manager import ConversationManager
    from focusproof.runtime.event_log import InMemoryEventLog

    audit_log = InMemoryEventLog()
    manager = ConversationManager(
        repository=repository,
        audit_log=audit_log,
        project_root=tmp_path,
        llm_factory=awaiting_user_llm,
    )
    manager.create("sess_wait", learning_goal)

    result = manager.run_review("sess_wait")

    assert result.reviewStatus == "awaiting_user"
    assert result.reviewResult is None
    assert result.agentQuestions
    assert not audit_log.get_by_type("sess_wait", "score.calculated")
    assert not audit_log.get_by_type("sess_wait", "review.completed")
    question_events = audit_log.get_by_type("sess_wait", "question.asked")
    assert question_events[-1].payload["questionId"] == result.agentQuestions[0][
        "questionId"
    ]
    manager.close("sess_wait")


def test_completed_review_score_is_owned_by_focusproof(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
    evidence: Evidence,
) -> None:
    from focusproof.domain.scoring import score_learning_session
    from focusproof.openhands_runtime.manager import ConversationManager
    from focusproof.openhands_runtime.tools.review_draft import ReviewDraftAction
    from focusproof.runtime.event_log import InMemoryEventLog

    audit_log = InMemoryEventLog()
    manager = ConversationManager(
        repository=repository,
        audit_log=audit_log,
        project_root=tmp_path,
        llm_factory=completed_review_llm,
    )
    manager.create("sess_score", learning_goal)
    repository.add_evidence("sess_score", evidence)
    manager.send_evidence("sess_score", evidence)

    result = manager.run_review("sess_score")
    expected = score_learning_session(learning_goal, [evidence], [])

    assert "score" not in ReviewDraftAction.model_fields
    assert result.reviewStatus == "completed"
    assert result.reviewResult is not None
    assert result.reviewResult.score == expected.score
    score_events = audit_log.get_by_type("sess_score", "score.calculated")
    review_events = audit_log.get_by_type("sess_score", "review.completed")
    assert len(score_events) == 1
    assert len(review_events) == 1
    assert score_events[0].sequence < review_events[0].sequence
    completed = result.reviewResult
    assert score_events[0].payload == {
        "score": completed.score,
        "confidence": completed.confidence,
        "status": completed.status,
        "dimensions": completed.dimensions,
        "findings": [finding.model_dump(mode="json") for finding in completed.findings],
        "evidenceRefs": [evidence.evidenceId],
    }
    assert review_events[0].payload == {
        "reviewId": review_events[0].payload["reviewId"],
        "summary": completed.summary,
        "nextStep": completed.nextStep,
        "scoreEventId": score_events[0].id,
    }
    manager.close("sess_score")


def test_scoring_failure_does_not_emit_review_completed(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
    evidence: Evidence,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from focusproof.openhands_runtime import result_extractor
    from focusproof.openhands_runtime.manager import ConversationManager
    from focusproof.runtime.event_log import InMemoryEventLog

    audit_log = InMemoryEventLog()
    manager = ConversationManager(
        repository=repository,
        audit_log=audit_log,
        project_root=tmp_path,
        llm_factory=completed_review_llm,
    )
    manager.create("sess_score_failure", learning_goal)
    repository.add_evidence("sess_score_failure", evidence)
    manager.send_evidence("sess_score_failure", evidence)

    def fail_scoring(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("scoring failed")

    monkeypatch.setattr(result_extractor, "score_learning_session", fail_scoring)

    with pytest.raises(RuntimeError, match="scoring failed"):
        manager.run_review("sess_score_failure")

    assert not audit_log.get_by_type("sess_score_failure", "score.calculated")
    assert not audit_log.get_by_type("sess_score_failure", "review.completed")
    manager.close("sess_score_failure")
