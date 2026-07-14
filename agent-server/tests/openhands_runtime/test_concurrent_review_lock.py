from __future__ import annotations

import asyncio
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
