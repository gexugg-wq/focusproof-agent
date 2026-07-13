import inspect
from pathlib import Path

from focusproof.openhands_adapter.real_conversation import _parse_review, run_real_learning_review_spike
from focusproof.openhands_adapter.safe_debug_tools import (
    assert_debug_tool_policy,
    list_disabled_openhands_tools,
)


def test_disabled_tools_include_dangerous_openhands_tools() -> None:
    disabled = list_disabled_openhands_tools()

    assert "TerminalTool" in disabled
    assert "FileEditorTool" in disabled
    assert "BrowserAutomation" in disabled
    assert "ApplyPatchTool" in disabled
    assert "WorkspaceMutationTool" in disabled
    assert_debug_tool_policy()


def test_real_learning_review_spike_without_key_returns_unavailable(tmp_path: Path) -> None:
    result = run_real_learning_review_spike(
        goal="Understand transaction hash",
        evidence="I saw 0x1234567890 but cannot explain it yet.",
        domain="web3",
        project_root=tmp_path,
    )

    assert result["mode"] == "unavailable"
    assert result["error"]
    assert result["disabledTools"]
    assert "credential" in result["error"]
    assert "sk-" not in repr(result)


def test_parse_review_accepts_openhands_markdown_action_summary() -> None:
    parsed = _parse_review(
        "**Action:** Ask Question | **Reason:** Evidence shows conceptual awareness | "
        "**Next Step:** Request concrete decoding walkthrough"
    )

    assert parsed["recommendedAction"] == "ask_question"


def test_parse_review_accepts_json_fields() -> None:
    parsed = _parse_review(
        '{"recommendedAction":"request_evidence",'
        '"question":"Can you explain the difference between tx hash and contract address?",'
        '"reason":"The evidence has a hash but not enough conceptual explanation."}'
    )

    assert parsed["recommendedAction"] == "request_evidence"
    assert parsed["question"] == "Can you explain the difference between tx hash and contract address?"
    assert parsed["reason"] == "The evidence has a hash but not enough conceptual explanation."


def test_parse_review_accepts_markdown_question_and_reason() -> None:
    parsed = _parse_review(
        "**Action:** Ask Question\n"
        "**Question:** What does the transaction hash identify, and how is it different from a contract address?\n"
        "**Reason:** The submitted evidence includes a hash but the learner says they are unsure about the distinction.\n"
        "**Next Step:** Answer the question in one paragraph."
    )

    assert parsed["recommendedAction"] == "ask_question"
    assert parsed["question"] == "What does the transaction hash identify, and how is it different from a contract address?"
    assert parsed["reason"] == "The submitted evidence includes a hash but the learner says they are unsure about the distinction."


def test_debug_runner_reuses_official_conversation_manager() -> None:
    source = inspect.getsource(run_real_learning_review_spike)

    assert "ConversationManager" in source
    assert "Conversation(" not in source
