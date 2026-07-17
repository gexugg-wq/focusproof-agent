from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any, cast

import pytest
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.testing import TestLLM

from focusproof.openhands_runtime.locks import FileSessionRunLock, SessionBusyError
from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.openhands_runtime.provider_admission import (
    BoundedProviderAdmission,
    ProviderAdmissionUnavailableError,
)
from focusproof.runtime.event_log import InMemoryEventLog
from focusproof.runtime.evidence import LearningGoal

from .conftest import SessionRepository


def test_lock_rejects_path_traversal(tmp_path: Path) -> None:
    lock = FileSessionRunLock(tmp_path, timeout_seconds=0.01)

    with pytest.raises(ValueError, match="unsafe"):
        with lock.acquire("../outside"):
            pass


def test_same_session_times_out_while_lock_is_held(tmp_path: Path) -> None:
    lock = FileSessionRunLock(tmp_path, timeout_seconds=0.05)
    entered = Event()
    release = Event()

    def holder() -> None:
        with lock.acquire("sess_1"):
            entered.set()
            release.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(holder)
        assert entered.wait(timeout=1)
        with pytest.raises(SessionBusyError) as captured:
            with lock.acquire("sess_1"):
                pass
        release.set()
        future.result(timeout=2)

    assert captured.value.session_id == "sess_1"


def test_different_sessions_do_not_share_a_lock(tmp_path: Path) -> None:
    lock = FileSessionRunLock(tmp_path, timeout_seconds=0.05)
    with lock.acquire("sess_1"):
        with lock.acquire("sess_2"):
            assert True


def test_lock_is_released_after_exception(tmp_path: Path) -> None:
    lock = FileSessionRunLock(tmp_path, timeout_seconds=0.05)
    with pytest.raises(RuntimeError):
        with lock.acquire("sess_1"):
            raise RuntimeError("abort")

    with lock.acquire("sess_1"):
        assert True


def test_two_concurrent_reviews_enter_conversation_run_once(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_lock = FileSessionRunLock(tmp_path / "var", timeout_seconds=0.05)
    manager = ConversationManager(
        repository=repository,
        audit_log=InMemoryEventLog(),
        project_root=tmp_path,
        llm_factory=lambda session_id: TestLLM.from_messages([]),
        run_lock=run_lock,
    )
    handle = manager.create("sess_concurrent", learning_goal)
    entered = Event()
    release = Event()
    run_count = 0
    original_arun = cast(
        Callable[[LocalConversation], Any],
        cast(Any, LocalConversation.arun),
    )

    async def blocking_arun(conversation: LocalConversation) -> None:
        nonlocal run_count
        if conversation is handle.conversation:
            run_count += 1
            entered.set()
            await asyncio.to_thread(release.wait, 2)
        await original_arun(conversation)

    monkeypatch.setattr(LocalConversation, "arun", blocking_arun)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(manager.run_review, "sess_concurrent")
        assert entered.wait(timeout=1)
        with pytest.raises(SessionBusyError):
            manager.run_review("sess_concurrent")
        release.set()
        first.result(timeout=2)
    assert run_count == 1
    manager.close_all()


def test_reviews_for_different_sessions_enter_native_runs_concurrently(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_lock = FileSessionRunLock(tmp_path / "var", timeout_seconds=0.2)
    manager = ConversationManager(
        repository=repository,
        audit_log=InMemoryEventLog(),
        project_root=tmp_path,
        llm_factory=lambda session_id: TestLLM.from_messages([]),
        run_lock=run_lock,
    )
    first_handle = manager.create("sess_parallel_a", learning_goal)
    second_handle = manager.create("sess_parallel_b", learning_goal)
    session_by_conversation = {
        id(first_handle.conversation): "sess_parallel_a",
        id(second_handle.conversation): "sess_parallel_b",
    }
    entered = {
        "sess_parallel_a": Event(),
        "sess_parallel_b": Event(),
    }
    release = Event()
    original_arun = cast(
        Callable[[LocalConversation], Any],
        cast(Any, LocalConversation.arun),
    )

    async def blocking_arun(conversation: LocalConversation) -> None:
        session_id = session_by_conversation[id(conversation)]
        entered[session_id].set()
        await asyncio.to_thread(release.wait, 2)
        await original_arun(conversation)

    monkeypatch.setattr(LocalConversation, "arun", blocking_arun)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(manager.run_review, "sess_parallel_a")
        second = executor.submit(manager.run_review, "sess_parallel_b")
        assert entered["sess_parallel_a"].wait(timeout=1)
        assert entered["sess_parallel_b"].wait(timeout=1)
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)

    manager.close_all()


def test_global_provider_admission_rejects_second_session_before_native_run(
    tmp_path: Path,
    repository: SessionRepository,
    learning_goal: LearningGoal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = BoundedProviderAdmission(
        max_concurrent=1,
        acquire_timeout_seconds=0.01,
    )
    manager = ConversationManager(
        repository=repository,
        audit_log=InMemoryEventLog(),
        project_root=tmp_path,
        llm_factory=lambda session_id: TestLLM.from_messages([]),
        run_lock=FileSessionRunLock(tmp_path / "var", timeout_seconds=0.2),
        provider_admission=admission,
    )
    first_handle = manager.create("sess_admitted", learning_goal)
    second_handle = manager.create("sess_rejected", learning_goal)
    entered = Event()
    release = Event()
    entered_conversations: list[int] = []

    async def blocking_arun(conversation: LocalConversation) -> None:
        entered_conversations.append(id(conversation))
        entered.set()
        await asyncio.to_thread(release.wait, 2)

    monkeypatch.setattr(LocalConversation, "arun", blocking_arun)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(manager.run_review, "sess_admitted")
            assert entered.wait(timeout=1)
            with pytest.raises(ProviderAdmissionUnavailableError):
                manager.run_review("sess_rejected")
            release.set()
            assert first.result(timeout=2).reviewStatus == "failed"

        assert entered_conversations == [id(first_handle.conversation)]
        assert id(second_handle.conversation) not in entered_conversations
    finally:
        release.set()
        manager.close_all()


def test_cancelling_waiting_review_does_not_interrupt_current_review(
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
        run_lock=FileSessionRunLock(tmp_path / "var", timeout_seconds=1),
    )
    manager.create("sess_cancel_contender", learning_goal)
    first_entered = Event()
    release_first = Event()
    primary_interrupted = Event()
    arun_calls = 0
    original_interrupt = cast(
        Callable[[LocalConversation], None],
        cast(Any, LocalConversation.interrupt),
    )

    async def blocking_arun(conversation: LocalConversation) -> None:
        nonlocal arun_calls
        del conversation
        arun_calls += 1
        first_entered.set()
        while not release_first.is_set():
            await asyncio.sleep(0.01)

    def record_interrupt(conversation: LocalConversation) -> None:
        primary_interrupted.set()
        original_interrupt(conversation)

    monkeypatch.setattr(LocalConversation, "arun", blocking_arun)
    monkeypatch.setattr(LocalConversation, "interrupt", record_interrupt)

    with ThreadPoolExecutor(max_workers=2) as executor:
        primary = executor.submit(
            manager.run_review,
            "sess_cancel_contender",
            None,
            "review-primary",
        )
        assert first_entered.wait(timeout=1)
        contender = executor.submit(
            manager.run_review,
            "sess_cancel_contender",
            None,
            "review-contender",
        )
        time.sleep(0.05)
        manager.interrupt("sess_cancel_contender", "review-contender")
        assert primary_interrupted.wait(timeout=0.1) is False
        release_first.set()
        assert primary.result(timeout=1).reviewStatus == "failed"
        assert contender.result(timeout=1).reviewStatus == "failed"

    assert arun_calls == 1
