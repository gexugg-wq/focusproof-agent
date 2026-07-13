from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM
import pytest

from focusproof.runtime.evidence import Evidence, LearningGoal


class EmptyRepository:
    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        raise KeyError((session_id, evidence_id))


def _goal() -> LearningGoal:
    return LearningGoal(
        domain="general",
        title="Learn event replay",
        goal="Explain append-only event replay.",
    )


def _test_llm(session_id: str) -> TestLLM:
    del session_id
    return TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Review finished")])]
    )


def test_conversation_handle_uses_sdk_compatible_uuid() -> None:
    from focusproof.openhands_runtime.handle import ConversationHandle

    assert ConversationHandle.model_fields["conversation_id"].annotation is UUID


def test_factory_creates_sdk_local_conversation_with_stable_uuid(
    tmp_path: Path,
) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    factory = ConversationFactory(
        project_root=tmp_path,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )

    handle = factory.create("sess_1", _goal())
    try:
        assert isinstance(handle.conversation, LocalConversation)
        assert handle.runtime_mode == "openhands-local-scripted-test"
        assert handle.conversation_id == uuid5(NAMESPACE_URL, "focusproof:sess_1")
        assert handle.workspace_path == tmp_path / "var/conversations/sess_1/workspace"
        assert handle.persistence_path == tmp_path / "var/conversations/sess_1/persistence"
    finally:
        handle.conversation.close()


def test_initialized_agent_contains_only_focusproof_tools(tmp_path: Path) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    factory = ConversationFactory(
        project_root=tmp_path,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )
    handle = factory.create("sess_tools", _goal())
    try:
        cast(Any, handle.conversation).send_message("initialize safe FocusProof tools")
        names = set(handle.conversation.agent.tools_map)
        assert names == {
            "focusproof_learner_input",
            "focusproof_review_draft",
            "focusproof_text_evidence_verification",
            "focusproof_url_evidence_verification",
        }
        forbidden = {
            "terminal",
            "file_editor",
            "browser",
            "browser_automation",
            "workspace_mutation",
            "apply_patch",
        }
        assert names.isdisjoint(forbidden)
    finally:
        handle.conversation.close()


def test_factory_narrows_verifiers_for_known_evidence_types(tmp_path: Path) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    factory = ConversationFactory(
        project_root=tmp_path,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )
    handle = factory.create("sess_text_tools", _goal(), evidence_types={"text"})
    try:
        cast(Any, handle.conversation).send_message("initialize text tools")
        assert set(handle.conversation.agent.tools_map) == {
            "focusproof_learner_input",
            "focusproof_review_draft",
            "focusproof_text_evidence_verification",
        }
    finally:
        handle.conversation.close()


def test_factory_uses_explicit_validated_data_directory(tmp_path: Path) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    data_dir = tmp_path / "durable-data"
    factory = ConversationFactory(
        project_root=tmp_path,
        data_dir=data_dir,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )
    handle = factory.create("sess_data_dir", _goal())
    try:
        assert handle.workspace_path == data_dir / "conversations/sess_data_dir/workspace"
        assert handle.persistence_path == data_dir / "conversations/sess_data_dir/persistence"
        assert handle.workspace_path.resolve().is_relative_to(data_dir.resolve())
    finally:
        handle.conversation.close()


def test_factory_propagates_verified_user_id_to_sdk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import focusproof.openhands_runtime.factory as factory_module

    captured: dict[str, object] = {}
    real_conversation = getattr(factory_module, "Conversation")

    def recording_conversation(**kwargs: Any) -> LocalConversation:
        captured["user_id"] = kwargs.get("user_id")
        return cast(LocalConversation, real_conversation(**kwargs))

    monkeypatch.setattr(factory_module, "Conversation", recording_conversation)
    factory = factory_module.ConversationFactory(
        project_root=tmp_path,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )
    handle = factory.create(
        "sess_identity",
        _goal(),
        user_id="verified-user-1",
    )
    try:
        assert captured == {"user_id": "verified-user-1"}
    finally:
        handle.conversation.close()
