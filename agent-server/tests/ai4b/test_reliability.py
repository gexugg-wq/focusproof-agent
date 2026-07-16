from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import time
from threading import Barrier, Event
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import MessageEvent, ObservationEvent
from focusproof.openhands_runtime.factory import RuntimeUnavailableError
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from starlette.types import Message as AsgiMessage, Scope
from sqlalchemy.exc import OperationalError

from focusproof.api import app as app_module
from focusproof.openhands_runtime.synchronizer import message_key_from_event
from focusproof.openhands_runtime.tools.verification import VerificationObservation
from focusproof.persistence.unit_of_work import SqlAlchemyUnitOfWork


def _lifecycle_llm(_: str) -> TestLLM:
    question = MessageToolCall(
        id="call_reliability_question",
        name="focusproof_learner_input",
        arguments=json.dumps(
            {
                "question": "How does replay preserve event identity?",
                "reason": "A specific explanation is required.",
                "requested_evidence_type": "text",
            }
        ),
        origin="completion",
    )
    verification = MessageToolCall(
        id="call_reliability_verify",
        name="focusproof_text_evidence_verification",
        arguments=json.dumps({"evidence_id": "ev_placeholder"}),
        origin="completion",
    )
    draft = MessageToolCall(
        id="call_reliability_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["The submitted explanation is specific."],
                "understanding_findings": ["The answer explains stable identity."],
                "contradictions": [],
                "recommended_next_step": "Add a replay-order example.",
                "confidence": 0.8,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Ask for an explanation")],
                tool_calls=[question],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Verify the evidence")],
                tool_calls=[verification],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Submit the review")],
                tool_calls=[draft],
            ),
        ]
    )


def _empty_llm(_: str) -> TestLLM:
    return TestLLM.from_messages([])


def _draft_llm(_: str) -> TestLLM:
    draft = MessageToolCall(
        id="call_reliability_failure_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["The evidence is specific."],
                "understanding_findings": ["The explanation identifies replay order."],
                "contradictions": [],
                "recommended_next_step": "Add one branch example.",
                "confidence": 0.8,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Submit the review")],
                tool_calls=[draft],
            )
        ]
    )


def _failed_verification_llm(_: str) -> TestLLM:
    verification = MessageToolCall(
        id="call_reliability_missing_evidence",
        name="focusproof_text_evidence_verification",
        arguments=json.dumps({"evidence_id": "ev_missing"}),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Verify missing evidence")],
                tool_calls=[verification],
            )
        ]
    )


def _create_session(client: TestClient) -> str:
    response = client.post(
        "/sessions",
        json={
            "domain": "general",
            "title": "Reliable replay",
            "goal": "Explain how stable event identity supports replay.",
        },
    )
    assert response.status_code == 200
    return str(response.json()["sessionId"])


def _assert_no_completed_review(client: TestClient, session_id: str) -> None:
    state = client.get(f"/sessions/{session_id}").json()["state"]
    events = client.get(f"/sessions/{session_id}/events").json()["events"]
    reviews = client.get(f"/sessions/{session_id}/reviews").json()["reviews"]

    assert state["status"] != "reviewed"
    assert state["reviewResult"] is None
    assert not any(event["type"] == "review.completed" for event in events)
    assert not any(review["reviewStatus"] == "completed" for review in reviews)


def test_completed_review_restart_preserves_all_persisted_identities(
    ai4b_app_factory: Callable[..., Any],
) -> None:
    with ai4b_app_factory(_lifecycle_llm) as first:
        session_id = _create_session(first.client)
        evidence = first.client.post(
            f"/sessions/{session_id}/evidence",
            json={
                "evidenceType": "text",
                "textContent": "Each native event keeps a stable ID during replay.",
            },
        )
        assert evidence.status_code == 200
        awaiting = first.client.post(f"/sessions/{session_id}/review")
        assert awaiting.json()["reviewStatus"] == "awaiting_user"
        question_id = awaiting.json()["agentQuestions"][0]["questionId"]
        answer = first.client.post(
            f"/sessions/{session_id}/answer",
            json={
                "questionId": question_id,
                "answer": "Replay reads immutable events in sequence without replacing IDs.",
            },
        )
        assert answer.status_code == 200
        completed = first.client.post(f"/sessions/{session_id}/review")
        assert completed.json()["reviewStatus"] == "completed"
        handle = first.app.state.conversation_manager.get(session_id)
        conversation_id = completed.json()["conversationId"]
        native_event_ids = [event.id for event in handle.conversation.state.events]
        projected = first.client.get(f"/sessions/{session_id}/events").json()["events"]
        reviews = first.client.get(f"/sessions/{session_id}/reviews").json()["reviews"]
        projected_identity = [(event["id"], event["sequence"]) for event in projected]
        review_ids = [review["reviewId"] for review in reviews]
        completed_result = completed.json()["reviewResult"]

    with ai4b_app_factory(_empty_llm) as restarted:
        retried = restarted.client.post(f"/sessions/{session_id}/review")
        restored_handle = restarted.app.state.conversation_manager.get(session_id)
        projected_after = restarted.client.get(f"/sessions/{session_id}/events").json()["events"]
        reviews_after = restarted.client.get(f"/sessions/{session_id}/reviews").json()["reviews"]

        assert retried.status_code == 200
        assert retried.json()["reviewStatus"] == "completed"
        assert retried.json()["conversationId"] == conversation_id
        assert retried.json()["reviewResult"] == completed_result
        assert [event.id for event in restored_handle.conversation.state.events] == native_event_ids
        assert [(event["id"], event["sequence"]) for event in projected_after] == projected_identity
        assert [review["reviewId"] for review in reviews_after] == review_ids


def test_llm_exception_before_tool_call_can_retry_without_false_completion(
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_arun = cast(
        Callable[[LocalConversation], Any],
        cast(Any, LocalConversation.arun),
    )
    attempts = 0

    async def fail_once(conversation: LocalConversation) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("LLM failed before its first tool call")
        await original_arun(conversation)

    monkeypatch.setattr(LocalConversation, "arun", fail_once)
    with ai4b_app_factory(_draft_llm) as running:
        session_id = _create_session(running.client)

        failed = running.client.post(f"/sessions/{session_id}/review")
        assert failed.status_code == 503
        assert failed.json()["reviewStatus"] == "failed"
        _assert_no_completed_review(running.client, session_id)

        retried = running.client.post(f"/sessions/{session_id}/review")
        assert retried.status_code == 200
        assert retried.json()["reviewStatus"] == "completed"
        assert attempts == 2


def test_structured_verification_failure_never_completes_review(
    ai4b_app_factory: Callable[..., Any],
) -> None:
    with ai4b_app_factory(_failed_verification_llm) as running:
        session_id = _create_session(running.client)

        failed = running.client.post(f"/sessions/{session_id}/review")
        handle = running.app.state.conversation_manager.get(session_id)
        observations = [
            event.observation
            for event in handle.conversation.state.events
            if isinstance(event, ObservationEvent)
            and isinstance(event.observation, VerificationObservation)
        ]

        assert failed.status_code == 503
        assert any(
            observation.status == "failed"
            and observation.error_code == "evidence_not_found"
            for observation in observations
        )
        _assert_no_completed_review(running.client, session_id)

    with ai4b_app_factory(_draft_llm) as restarted:
        retried = restarted.client.post(f"/sessions/{session_id}/review")
        assert retried.status_code == 200
        assert retried.json()["reviewStatus"] == "completed"


def test_operational_error_rolls_back_and_retry_persists_one_completion(
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_commit = SqlAlchemyUnitOfWork.commit
    failures = 0

    def fail_commit_once(self: SqlAlchemyUnitOfWork) -> None:
        nonlocal failures
        if failures == 0:
            failures += 1
            raise OperationalError(
                "COMMIT",
                {},
                RuntimeError("injected commit boundary failure"),
            )
        original_commit(self)

    with ai4b_app_factory(_draft_llm) as running:
        session_id = _create_session(running.client)
        monkeypatch.setattr(SqlAlchemyUnitOfWork, "commit", fail_commit_once)

        failed = running.client.post(f"/sessions/{session_id}/review")
        assert failed.status_code == 503
        assert failed.json() == {"code": "database_unavailable", "retryable": True}
        _assert_no_completed_review(running.client, session_id)

        retried = running.client.post(f"/sessions/{session_id}/review")
        reviews = running.client.get(f"/sessions/{session_id}/reviews").json()["reviews"]
        events = running.client.get(f"/sessions/{session_id}/events").json()["events"]

        assert retried.status_code == 200
        assert retried.json()["reviewStatus"] == "completed"
        assert sum(review["reviewStatus"] == "completed" for review in reviews) == 1
        assert sum(event["type"] == "review.completed" for event in events) == 1


def test_review_timeout_fails_without_completed_facts_and_releases_run_lock(
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = Event()
    release = Event()
    original_arun = LocalConversation.arun

    async def blocking_arun(conversation: LocalConversation) -> None:
        del conversation
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(LocalConversation, "arun", blocking_arun)
    with ai4b_app_factory(
        _draft_llm,
        review_timeout_seconds=0.05,
    ) as running:
        session_id = _create_session(running.client)
        timed_out_at_client = False
        response = None

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                running.client.post,
                f"/sessions/{session_id}/review",
            )
            assert entered.wait(timeout=1)
            try:
                response = future.result(timeout=0.3)
            except FutureTimeoutError:
                timed_out_at_client = True
            finally:
                release.set()
            if timed_out_at_client:
                future.result(timeout=1)

        assert timed_out_at_client is False
        assert response is not None
        assert response.status_code == 503
        _assert_no_completed_review(running.client, session_id)
        monkeypatch.setattr(LocalConversation, "arun", original_arun)
        retry = running.client.post(f"/sessions/{session_id}/review")
        assert retry.status_code == 200
        assert retry.json()["reviewStatus"] == "completed"


def test_cancelled_review_request_interrupts_the_native_conversation(
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = Event()
    release = Event()
    interrupted = Event()
    exited = Event()
    original_arun = LocalConversation.arun
    original_interrupt = LocalConversation.interrupt

    async def blocking_arun(conversation: LocalConversation) -> None:
        del conversation
        try:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        finally:
            exited.set()

    def record_interrupt(conversation: LocalConversation) -> None:
        interrupted.set()
        original_interrupt(conversation)

    monkeypatch.setattr(LocalConversation, "arun", blocking_arun)
    monkeypatch.setattr(LocalConversation, "interrupt", record_interrupt)
    with ai4b_app_factory(_draft_llm) as running:
        session_id = _create_session(running.client)

        async def scenario() -> bool:
            transport = httpx.ASGITransport(app=running.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                request = asyncio.create_task(
                    client.post(f"/sessions/{session_id}/review")
                )
                assert await asyncio.to_thread(entered.wait, 1)
                request.cancel()
                await asyncio.sleep(0.05)
                cancelled_promptly = request.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await request
                return cancelled_promptly

        cancelled_promptly = asyncio.run(scenario())
        assert cancelled_promptly is True
        assert interrupted.wait(timeout=0.5)
        assert exited.wait(timeout=0.5)
        _assert_no_completed_review(running.client, session_id)
        monkeypatch.setattr(LocalConversation, "arun", original_arun)
        retry = running.client.post(f"/sessions/{session_id}/review")
        assert retry.status_code == 200
        assert retry.json()["reviewStatus"] == "completed"


def test_concurrent_identical_answer_allows_retryable_503_and_safe_retry(
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with ai4b_app_factory(_empty_llm) as running:
        session_id = _create_session(running.client)
        original_commit = SqlAlchemyUnitOfWork.commit
        failures = 0

        def fail_one_contender(self: SqlAlchemyUnitOfWork) -> None:
            nonlocal failures
            if failures == 0:
                failures += 1
                raise OperationalError(
                    "COMMIT",
                    {},
                    RuntimeError("injected concurrent SQLite failure"),
                )
            original_commit(self)

        monkeypatch.setattr(
            SqlAlchemyUnitOfWork,
            "commit",
            fail_one_contender,
        )
        start = Barrier(2)
        payload = {
            "questionId": "q_concurrent",
            "answer": "The same immutable answer is safe to replay.",
        }

        def submit() -> httpx.Response:
            start.wait(timeout=1)
            return cast(
                httpx.Response,
                running.client.post(
                    f"/sessions/{session_id}/answer",
                    json=payload,
                ),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = [
                future.result(timeout=2)
                for future in (executor.submit(submit), executor.submit(submit))
            ]

        assert sorted(response.status_code for response in responses) == [200, 503]
        unavailable = next(response for response in responses if response.status_code == 503)
        assert unavailable.json() == {
            "code": "database_unavailable",
            "retryable": True,
        }

        retried = running.client.post(
            f"/sessions/{session_id}/answer",
            json=payload,
        )
        assert retried.status_code == 200
        with running.app.state.uow_factory() as uow:
            answers = uow.answers.list_for_session(session_id)
        events = running.client.get(f"/sessions/{session_id}/events").json()["events"]
        handle = running.app.state.conversation_manager.get(session_id)
        native_keys = [
            message_key_from_event(event)
            for event in handle.conversation.state.events
            if isinstance(event, MessageEvent)
        ]
        native_answer_keys = [
            key
            for key in native_keys
            if key is not None and key.startswith("answer:")
        ]

        assert len(answers) == 1
        assert answers[0].version == 1
        assert sum(event["type"] == "answer.submitted" for event in events) == 1
        assert native_answer_keys == [f"answer:{session_id}:q_concurrent:1"]


def test_app_shutdown_releases_provider_registry_and_engine_exactly_once(
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_releases = 0
    engine_disposals = 0

    def release_provider() -> None:
        nonlocal provider_releases
        provider_releases += 1

    monkeypatch.setattr(app_module, "release_repository_provider", release_provider)
    with ai4b_app_factory(_empty_llm) as running:
        engine = running.app.state.engine
        original_dispose = engine.dispose

        def dispose_engine() -> None:
            nonlocal engine_disposals
            engine_disposals += 1
            original_dispose()

        monkeypatch.setattr(engine, "dispose", dispose_engine)
        _create_session(running.client)

    assert provider_releases == 1
    assert engine_disposals == 1


def test_shutdown_waits_for_admitted_restore_before_returning(
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with ai4b_app_factory(_empty_llm) as running:
        session_id = _create_session(running.client)
        manager = running.app.state.conversation_manager
        manager.close(session_id)
        factory = cast(Any, manager)._factory
        original_create = factory.create
        create_entered = Event()
        release_create = Event()

        def blocking_create(*args: Any, **kwargs: Any) -> Any:
            create_entered.set()
            assert release_create.wait(timeout=1)
            return original_create(*args, **kwargs)

        monkeypatch.setattr(factory, "create", blocking_create)
        with ThreadPoolExecutor(max_workers=2) as executor:
            review = executor.submit(
                manager.run_review,
                session_id,
                "dev-anonymous-user",
            )
            assert create_entered.wait(timeout=1)
            shutdown = executor.submit(manager.close_all)
            try:
                time.sleep(0.05)
                assert shutdown.done() is False
            finally:
                release_create.set()
            with pytest.raises(RuntimeUnavailableError, match="shutting down"):
                review.result(timeout=1)
            shutdown.result(timeout=1)


def test_cancellation_before_restore_is_not_lost_or_run_after_cancel(
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with ai4b_app_factory(_draft_llm) as running:
        session_id = _create_session(running.client)
        manager = running.app.state.conversation_manager
        manager.close(session_id)
        factory = cast(Any, manager)._factory
        original_create = factory.create
        create_entered = Event()
        release_create = Event()
        create_finished = Event()
        interrupted = Event()
        arun_calls = 0
        original_interrupt = LocalConversation.interrupt

        def blocking_create(*args: Any, **kwargs: Any) -> Any:
            create_entered.set()
            assert release_create.wait(timeout=1)
            handle = original_create(*args, **kwargs)
            create_finished.set()
            return handle

        async def record_arun(conversation: LocalConversation) -> None:
            nonlocal arun_calls
            del conversation
            arun_calls += 1

        def record_interrupt(conversation: LocalConversation) -> None:
            interrupted.set()
            original_interrupt(conversation)

        monkeypatch.setattr(factory, "create", blocking_create)
        monkeypatch.setattr(LocalConversation, "arun", record_arun)
        monkeypatch.setattr(LocalConversation, "interrupt", record_interrupt)

        async def scenario() -> None:
            transport = httpx.ASGITransport(app=running.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                request = asyncio.create_task(
                    client.post(f"/sessions/{session_id}/review")
                )
                assert await asyncio.to_thread(create_entered.wait, 1)
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
                release_create.set()
                assert await asyncio.to_thread(create_finished.wait, 1)
                await asyncio.sleep(0.1)

        asyncio.run(scenario())
        assert interrupted.wait(timeout=0.2)
        assert arun_calls == 0
        _assert_no_completed_review(running.client, session_id)


def test_http_disconnect_interrupts_review_without_task_cancellation(
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = Event()
    interrupted = Event()
    force_release = Event()
    original_interrupt = LocalConversation.interrupt

    async def blocking_arun(conversation: LocalConversation) -> None:
        del conversation
        entered.set()
        while not interrupted.is_set() and not force_release.is_set():
            await asyncio.sleep(0.01)

    def record_interrupt(conversation: LocalConversation) -> None:
        interrupted.set()
        original_interrupt(conversation)

    monkeypatch.setattr(LocalConversation, "arun", blocking_arun)
    monkeypatch.setattr(LocalConversation, "interrupt", record_interrupt)
    with ai4b_app_factory(_draft_llm) as running:
        session_id = _create_session(running.client)

        async def scenario() -> bool:
            incoming: asyncio.Queue[AsgiMessage] = asyncio.Queue()
            sent: list[AsgiMessage] = []
            await incoming.put(
                {"type": "http.request", "body": b"", "more_body": False}
            )
            scope: Scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": f"/sessions/{session_id}/review",
                "raw_path": f"/sessions/{session_id}/review".encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [(b"content-length", b"0")],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            }

            async def receive() -> AsgiMessage:
                return await incoming.get()

            async def send(message: AsgiMessage) -> None:
                sent.append(message)

            request = asyncio.create_task(running.app(scope, receive, send))
            assert await asyncio.to_thread(entered.wait, 1)
            await incoming.put({"type": "http.disconnect"})
            await asyncio.sleep(0.1)
            handled = request.done() and interrupted.is_set()
            if not request.done():
                force_release.set()
                request.cancel()
            with suppress(asyncio.CancelledError):
                await request
            return handled

        assert asyncio.run(scenario()) is True
        _assert_no_completed_review(running.client, session_id)


def test_app_shutdown_releases_resources_when_manager_close_fails(
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_releases = 0
    engine_disposals = 0

    def release_provider() -> None:
        nonlocal provider_releases
        provider_releases += 1

    monkeypatch.setattr(app_module, "release_repository_provider", release_provider)
    context = ai4b_app_factory(_empty_llm)
    with pytest.raises(RuntimeError, match="close failed"):
        with context as running:
            manager = running.app.state.conversation_manager
            engine = running.app.state.engine
            original_dispose = engine.dispose

            def fail_close() -> None:
                raise RuntimeError("close failed")

            def dispose_engine() -> None:
                nonlocal engine_disposals
                engine_disposals += 1
                original_dispose()

            monkeypatch.setattr(manager, "close_all", fail_close)
            monkeypatch.setattr(engine, "dispose", dispose_engine)

    assert provider_releases == 1
    assert engine_disposals == 1
