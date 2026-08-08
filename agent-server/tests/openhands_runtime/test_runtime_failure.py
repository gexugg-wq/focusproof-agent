from pathlib import Path

import pytest
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM

from focusproof.runtime.evidence import Evidence, LearningGoal


class EmptyRepository:
    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        raise KeyError((session_id, evidence_id))


def _test_llm(session_id: str) -> TestLLM:
    del session_id
    return TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="done")])]
    )


def test_sdk_conversation_creation_failure_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from focusproof.openhands_runtime import factory as factory_module
    from focusproof.openhands_runtime.factory import (
        ConversationFactory,
        RuntimeCreationError,
    )

    def fail_conversation(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("sdk constructor failed")

    monkeypatch.setattr(
        factory_module,
        "Conversation",
        fail_conversation,
    )
    factory = ConversationFactory(
        repository=EmptyRepository(),
        compatibility_mode=True,
        project_root=tmp_path,
        llm_factory=_test_llm,
    )

    with pytest.raises(RuntimeCreationError, match="LocalConversation"):
        factory.create(
            "sess_failure",
            LearningGoal(domain="general", title="Failure", goal="Fail safely"),
        )


def test_run_failure_never_reports_openhands_usage(tmp_path: Path) -> None:
    from focusproof.openhands_runtime.manager import ConversationManager
    from focusproof.runtime.audit_projection import InMemoryAuditProjectionStore

    def exhausted_llm(session_id: str) -> TestLLM:
        del session_id
        return TestLLM.from_messages([])

    manager = ConversationManager(
        repository=EmptyRepository(),
        audit_log=InMemoryAuditProjectionStore(),
        project_root=tmp_path,
        llm_factory=exhausted_llm,
    )
    manager.create(
        "sess_run_failure",
        LearningGoal(domain="general", title="Failure", goal="Fail safely"),
    )

    result = manager.run_review("sess_run_failure")

    assert result.conversationMode == "failed"
    assert result.usedOpenHandsConversation is False
    assert result.reviewStatus == "failed"
    assert result.reviewResult is None
    assert result.error
    manager.close("sess_run_failure")
