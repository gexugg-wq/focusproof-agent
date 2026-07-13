from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from openhands.sdk.tool import list_registered_tools

from focusproof.openhands_runtime import tool_registry
from focusproof.openhands_runtime.factory import ConversationFactory
from focusproof.openhands_runtime.tools.url_fetcher import FetchedUrl
from focusproof.runtime.evidence import LearningGoal

from .conftest import SessionRepository, completed_review_llm


class StubUrlFetcher:
    def fetch(self, source_url: str) -> FetchedUrl:
        raise AssertionError(f"unexpected fetch: {source_url}")


def test_registry_growth_is_bounded_across_one_hundred_conversations(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = set(list_registered_tools())
    calls: list[str] = []
    real_register = tool_registry._register_tool

    def counted_register(name: str, definition: Any) -> None:
        calls.append(name)
        real_register(name, definition)

    monkeypatch.setattr(tool_registry, "_register_tool", counted_register)
    factory = ConversationFactory(
        project_root=tmp_path,
        repository=repository,
        llm_factory=completed_review_llm,
    )
    handles = []
    try:
        for index in range(100):
            handle = factory.create(f"sess_registry_{index}", learning_goal)
            handles.append(handle)
            cast(Any, handle.conversation).send_message("initialize tools")
            assert set(handle.conversation.agent.tools_map) == {
                "focusproof_learner_input",
                "focusproof_review_draft",
                "focusproof_text_evidence_verification",
                "focusproof_url_evidence_verification",
            }
    finally:
        for handle in handles:
            handle.conversation.close()

    after = set(list_registered_tools())
    expected = {
        "FocusProofEvidenceVerificationTool",
        "FocusProofLearnerInputTool",
        "FocusProofReviewDraftTool",
        "FocusProofTextEvidenceVerificationTool",
        "FocusProofUrlEvidenceVerificationTool",
    }
    initially_missing = expected - before
    assert expected <= after
    assert after - before == initially_missing
    assert set(calls) == initially_missing
    assert len(calls) <= 5
    assert after.isdisjoint(
        {"Terminal", "FileEditor", "Browser", "ApplyPatch"}
    )


def test_repository_provider_can_be_released(repository: SessionRepository) -> None:
    fetcher = StubUrlFetcher()
    tool_registry.configure_repository_provider(repository)
    tool_registry.configure_url_fetcher_provider(fetcher)
    assert tool_registry.get_repository_provider() is repository
    assert tool_registry.get_url_fetcher_provider() is fetcher

    tool_registry.release_repository_provider()

    try:
        tool_registry.get_repository_provider()
    except RuntimeError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("released repository provider remained available")
    with pytest.raises(RuntimeError, match="not configured"):
        tool_registry.get_url_fetcher_provider()
