from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any, cast

import pytest
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.testing import TestLLM

from focusproof.openhands_runtime.factory import RuntimeUnavailableError
from focusproof.openhands_runtime.locks import FileSessionRunLock, SessionBusyError
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


def test_shutdown_rejects_new_review_then_interrupts_and_closes_inflight_run(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ConversationManager(
        repository=repository,
        audit_log=InMemoryEventLog(),
        project_root=tmp_path,
        llm_factory=lambda session_id: TestLLM.from_messages([]),
        run_lock=FileSessionRunLock(tmp_path / "var", timeout_seconds=0.05),
    )
    manager.create("sess_shutdown_inflight", learning_goal)
    entered = Event()
    interrupted = Event()
    force_release = Event()
    close_calls = 0
    original_interrupt = cast(
        Callable[[LocalConversation], None],
        cast(Any, LocalConversation.interrupt),
    )
    original_close = cast(
        Callable[[LocalConversation], None],
        cast(Any, LocalConversation.close),
    )

    async def blocking_arun(conversation: LocalConversation) -> None:
        del conversation
        entered.set()
        while not interrupted.is_set() and not force_release.is_set():
            await asyncio.sleep(0.01)

    def record_interrupt(conversation: LocalConversation) -> None:
        interrupted.set()
        original_interrupt(conversation)

    def record_close(conversation: LocalConversation) -> None:
        nonlocal close_calls
        close_calls += 1
        original_close(conversation)

    monkeypatch.setattr(LocalConversation, "arun", blocking_arun)
    monkeypatch.setattr(LocalConversation, "interrupt", record_interrupt)
    monkeypatch.setattr(LocalConversation, "close", record_close)

    with ThreadPoolExecutor(max_workers=2) as executor:
        review = executor.submit(manager.run_review, "sess_shutdown_inflight")
        assert entered.wait(timeout=1)
        with pytest.raises(SessionBusyError):
            manager.run_review("sess_shutdown_inflight")
        shutdown = executor.submit(manager.close_all)
        deadline = time.monotonic() + 0.5
        while manager._accepting_reviews and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            with pytest.raises(RuntimeUnavailableError, match="shutting down"):
                manager.run_review("sess_shutdown_inflight")
            assert interrupted.wait(timeout=0.2)
        finally:
            force_release.set()
        review.result(timeout=1)
        shutdown.result(timeout=1)

    assert close_calls == 1
    with pytest.raises(RuntimeUnavailableError, match="shutting down"):
        manager.run_review("sess_shutdown_inflight")


def test_shutdown_waits_past_lock_timeout_for_interrupted_review_to_exit(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ConversationManager(
        repository=repository,
        audit_log=InMemoryEventLog(),
        project_root=tmp_path,
        llm_factory=lambda session_id: TestLLM.from_messages([]),
        run_lock=FileSessionRunLock(tmp_path / "var", timeout_seconds=0.02),
    )
    manager.create("sess_slow_interrupt", learning_goal)
    entered = Event()
    interrupted = Event()
    close_calls = 0
    original_interrupt = cast(
        Callable[[LocalConversation], None],
        cast(Any, LocalConversation.interrupt),
    )
    original_close = cast(
        Callable[[LocalConversation], None],
        cast(Any, LocalConversation.close),
    )

    async def slowly_stopping_arun(conversation: LocalConversation) -> None:
        del conversation
        entered.set()
        while not interrupted.is_set():
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.08)

    def record_interrupt(conversation: LocalConversation) -> None:
        interrupted.set()
        original_interrupt(conversation)

    def record_close(conversation: LocalConversation) -> None:
        nonlocal close_calls
        close_calls += 1
        original_close(conversation)

    monkeypatch.setattr(LocalConversation, "arun", slowly_stopping_arun)
    monkeypatch.setattr(LocalConversation, "interrupt", record_interrupt)
    monkeypatch.setattr(LocalConversation, "close", record_close)

    with ThreadPoolExecutor(max_workers=2) as executor:
        review = executor.submit(manager.run_review, "sess_slow_interrupt")
        assert entered.wait(timeout=1)
        shutdown = executor.submit(manager.close_all)
        assert interrupted.wait(timeout=0.2)
        review.result(timeout=1)
        shutdown.result(timeout=1)

    assert close_calls == 1
