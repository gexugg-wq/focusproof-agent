from dataclasses import replace
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


def test_empty_conversation_persistence_directory_is_not_a_restore(
    tmp_path: Path,
) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    conversation_id = uuid5(NAMESPACE_URL, "focusproof:sess_empty_store")
    empty_store = (
        tmp_path
        / "var/conversations/sess_empty_store/persistence"
        / conversation_id.hex
    )
    empty_store.mkdir(parents=True)
    factory = ConversationFactory(
        project_root=tmp_path,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )

    handle = factory.create(
        "sess_empty_store",
        _goal(),
        conversation_id=conversation_id,
    )
    try:
        assert handle.compatibility_restore is False
        cast(Any, handle.conversation).send_message("initialize fresh tools")
        assert "focusproof_evidence_verification" not in (
            handle.conversation.agent.tools_map
        )
    finally:
        handle.conversation.close()


def test_ai4a_restore_uses_legacy_compatibility_superset(tmp_path: Path) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    factory = ConversationFactory(
        project_root=tmp_path,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )
    initial = factory.create("sess_ai4a_restore", _goal())
    conversation_id = initial.conversation_id
    assert initial.compatibility_restore is False
    initial.conversation.close()

    restored = factory.create(
        "sess_ai4a_restore",
        _goal(),
        conversation_id=conversation_id,
    )
    try:
        assert restored.compatibility_restore is True
        cast(Any, restored.conversation).send_message("initialize restored tools")
        assert set(restored.conversation.agent.tools_map) == {
            "focusproof_evidence_verification",
            "focusproof_learner_input",
            "focusproof_review_draft",
            "focusproof_text_evidence_verification",
            "focusproof_url_evidence_verification",
        }
    finally:
        restored.conversation.close()


def test_restore_does_not_narrow_tools_from_persisted_default_set(
    tmp_path: Path,
) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    factory = ConversationFactory(
        project_root=tmp_path,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )
    initial = factory.create("sess_restore_narrow", _goal())
    conversation_id = initial.conversation_id
    initial.conversation.close()

    restored = factory.create(
        "sess_restore_narrow",
        _goal(),
        conversation_id=conversation_id,
        evidence_types={"text"},
    )
    try:
        cast(Any, restored.conversation).send_message("initialize restored tools")
        assert set(restored.conversation.agent.tools_map) == {
            "focusproof_evidence_verification",
            "focusproof_learner_input",
            "focusproof_review_draft",
            "focusproof_text_evidence_verification",
            "focusproof_url_evidence_verification",
        }
    finally:
        restored.conversation.close()


def test_factory_rejects_runtime_path_resolving_outside_data_dir(
    tmp_path: Path,
) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir()
    conversations = data_dir / "conversations"
    conversations.mkdir(parents=True)
    (conversations / "sess_symlink").symlink_to(outside, target_is_directory=True)
    factory = ConversationFactory(
        project_root=tmp_path,
        data_dir=data_dir,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )

    with pytest.raises(ValueError, match="outside FOCUSPROOF_DATA_DIR"):
        factory.create("sess_symlink", _goal())


def test_factory_records_toolset_version_on_fresh_conversation(tmp_path: Path) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    factory = ConversationFactory(
        project_root=tmp_path,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )
    handle = factory.create("sess_version", _goal(), evidence_types={"text"})
    try:
        assert len(handle.toolset_version) == 12
        assert handle.persisted_toolset_version == handle.toolset_version
        assert handle.toolset_version_mismatch is False
        assert handle.conversation.state.tags["toolsetversion"] == (
            handle.toolset_version
        )
    finally:
        handle.conversation.close()


def test_factory_reports_persisted_toolset_version_mismatch(tmp_path: Path) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    factory = ConversationFactory(
        project_root=tmp_path,
        repository=EmptyRepository(),
        llm_factory=_test_llm,
    )
    initial = factory.create(
        "sess_version_restore",
        _goal(),
        evidence_types={"text"},
    )
    initial_version = initial.toolset_version
    initial.conversation.close()

    restored = factory.create("sess_version_restore", _goal())
    try:
        assert restored.persisted_toolset_version == initial_version
        assert restored.toolset_version != initial_version
        assert restored.toolset_version_mismatch is True
    finally:
        restored.conversation.close()


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


def test_factory_passes_url_capability_timeout_to_default_fetcher(
    tmp_path: Path,
) -> None:
    from focusproof.openhands_runtime.capabilities import (
        VerificationCapabilityRegistry,
        build_builtin_capabilities,
    )
    from focusproof.openhands_runtime.factory import ConversationFactory
    from focusproof.openhands_runtime.tool_registry import (
        get_url_fetcher_provider,
        release_repository_provider,
    )
    from focusproof.openhands_runtime.tools.url_fetcher import BoundedUrlFetcher

    capabilities = tuple(
        replace(item, timeout_seconds=2.5)
        if item.registry_name == "url"
        else item
        for item in build_builtin_capabilities()
    )
    try:
        ConversationFactory(
            project_root=tmp_path,
            repository=EmptyRepository(),
            llm_factory=_test_llm,
            capability_registry=VerificationCapabilityRegistry(capabilities),
        )
        fetcher = get_url_fetcher_provider()
        assert isinstance(fetcher, BoundedUrlFetcher)
        assert fetcher.total_timeout_seconds == 2.5
    finally:
        release_repository_provider()
