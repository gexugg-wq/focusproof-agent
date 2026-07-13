from __future__ import annotations

import re
from pathlib import Path

import pytest
from openhands.sdk.testing import TestLLM

from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.runtime.event_log import InMemoryEventLog
from focusproof.runtime.evidence import LearningGoal

from .conftest import SessionRepository


def test_focusproof_business_code_does_not_assign_execution_status() -> None:
    runtime_root = Path(__file__).resolve().parents[2] / "focusproof/openhands_runtime"
    assignment = re.compile(r"\.execution_status\s*=")
    offenders = [
        path
        for path in runtime_root.rglob("*.py")
        if assignment.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_close_all_closes_handles_and_rejects_new_review(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
) -> None:
    manager = ConversationManager(
        repository=repository,
        audit_log=InMemoryEventLog(),
        project_root=tmp_path,
        llm_factory=lambda session_id: TestLLM.from_messages([]),
    )
    manager.create("sess_shutdown", learning_goal)

    manager.close_all()

    with pytest.raises(RuntimeError, match="shutting down"):
        manager.run_review("sess_shutdown")
