from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Iterator
from ipaddress import ip_address
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from typing import Any, Protocol, cast
from uuid import uuid4

import httpx
import pytest
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import AgentErrorEvent, InterruptEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM

from focusproof.openhands_runtime.factory import ConversationFactory
from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.openhands_runtime.projector import OpenHandsEventProjector
from focusproof.openhands_runtime.tools.url_evidence import (
    UrlEvidenceVerificationExecutor,
)
from focusproof.openhands_runtime.tools.url_fetcher import (
    BoundedUrlFetcher,
    FetchedUrl,
)
from focusproof.openhands_runtime.tools.url_safety import Address, UrlSafetyPolicy
from focusproof.openhands_runtime.tools.verification import EvidenceReferenceAction
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.event_log import InMemoryEventLog

from .conftest import SessionRepository


PUBLIC_ADDRESS = ip_address("93.184.216.34")


class _AsyncRunnableConversation(Protocol):
    def arun(self) -> Awaitable[None]: ...


def _client(transport: httpx.BaseTransport) -> httpx.Client:
    return httpx.Client(
        transport=transport,
        follow_redirects=False,
        timeout=httpx.Timeout(1.0),
    )


def _url_evidence(
    evidence_id: str,
    source_url: str = "https://example.com/private/path?token=secret#fragment",
) -> Evidence:
    return Evidence(
        evidenceId=evidence_id,
        evidenceType="url",
        contentHash=f"sha256:{evidence_id}",
        sourceUrl=source_url,
    )


def _executor(
    session_id: str,
    evidence: Evidence,
    fetcher: BoundedUrlFetcher,
) -> UrlEvidenceVerificationExecutor:
    repository = SessionRepository()
    repository.add_evidence(session_id, evidence)
    return UrlEvidenceVerificationExecutor(repository, session_id, fetcher)


def test_resolver_delay_obeys_true_wall_clock_total_timeout() -> None:
    budget = 0.05

    def slow_resolver(hostname: str) -> tuple[Address, ...]:
        del hostname
        sleep(0.2)
        return (PUBLIC_ADDRESS,)

    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                content=b"unexpected",
                request=request,
            )
        )
    )
    try:
        fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=slow_resolver),
            client=client,
            total_timeout_seconds=budget,
        )
        executor = _executor("sess_slow_dns", _url_evidence("ev_dns"), fetcher)
        started = monotonic()
        result = executor(EvidenceReferenceAction(evidence_id="ev_dns"))
        elapsed = monotonic() - started
    finally:
        client.close()

    assert result.status == "inconclusive"
    assert result.error_code == "network_timeout"
    assert elapsed < 0.15


class _SlowDripStream(httpx.SyncByteStream):
    def __init__(self) -> None:
        self.closed = Event()

    def __iter__(self) -> Iterator[bytes]:
        for _ in range(5):
            sleep(0.2)
            yield b"x"

    def close(self) -> None:
        self.closed.set()


def test_slow_drip_response_obeys_true_wall_clock_total_timeout() -> None:
    stream = _SlowDripStream()
    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                stream=stream,
                request=request,
            )
        )
    )
    try:
        fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(
                allow_http=False,
                resolver=lambda hostname: (PUBLIC_ADDRESS,),
            ),
            client=client,
            total_timeout_seconds=0.05,
        )
        executor = _executor("sess_drip", _url_evidence("ev_drip"), fetcher)
        started = monotonic()
        result = executor(EvidenceReferenceAction(evidence_id="ev_drip"))
        elapsed = monotonic() - started
    finally:
        client.close()

    assert result.status == "inconclusive"
    assert result.error_code == "network_timeout"
    assert elapsed < 0.15


def test_url_executor_interrupt_and_close_are_idempotent() -> None:
    client = _client(httpx.MockTransport(lambda request: httpx.Response(500)))
    try:
        fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(
                allow_http=False,
                resolver=lambda hostname: (PUBLIC_ADDRESS,),
            ),
            client=client,
            total_timeout_seconds=0.05,
        )
        executor = _executor("sess_interrupt", _url_evidence("ev_interrupt"), fetcher)
        executor.interrupt()
        executor.interrupt()
        executor.close()
        executor.close()
    finally:
        client.close()


def test_timed_out_session_does_not_close_shared_client_for_other_session() -> None:
    def resolver(hostname: str) -> tuple[Address, ...]:
        if hostname == "slow.example":
            sleep(0.2)
        return (PUBLIC_ADDRESS,)

    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                content=b"session b remains usable",
                request=request,
            )
        )
    )
    try:
        shared_fetcher = BoundedUrlFetcher(
            policy=UrlSafetyPolicy(allow_http=False, resolver=resolver),
            client=client,
            total_timeout_seconds=0.05,
        )
        session_a = _executor(
            "sess_a",
            _url_evidence("ev_a", "https://slow.example/private?a=secret"),
            shared_fetcher,
        )
        session_b = _executor(
            "sess_b",
            _url_evidence("ev_b", "https://fast.example/public"),
            shared_fetcher,
        )
        result_a = session_a(EvidenceReferenceAction(evidence_id="ev_a"))
        result_b = session_b(EvidenceReferenceAction(evidence_id="ev_b"))
    finally:
        client.close()

    assert result_a.error_code == "network_timeout"
    assert result_b.status == "success"


def test_timeout_observation_never_contains_raw_url_secrets() -> None:
    class _TimeoutFetcher:
        total_timeout_seconds = 0.01

        def fetch(self, source_url: str) -> FetchedUrl:
            del source_url
            sleep(0.1)
            raise AssertionError("isolated work must not determine the result")

    source_url = (
        "https://credential:password@example.com/private/path"
        "?token=query-secret#private-fragment"
    )
    repository = SessionRepository()
    repository.add_evidence("sess_private", _url_evidence("ev_private", source_url))
    executor = UrlEvidenceVerificationExecutor(
        repository,
        "sess_private",
        _TimeoutFetcher(),
    )

    result = executor(EvidenceReferenceAction(evidence_id="ev_private"))
    native = ObservationEvent(
        tool_name="focusproof_url_evidence_verification",
        tool_call_id="call_private",
        observation=result,
        action_id="action_private",
    )
    projected = OpenHandsEventProjector(
        "sess_private",
        uuid4(),
        InMemoryEventLog(),
    ).on_event(native)
    assert projected is not None
    serialized = result.model_dump_json() + json.dumps(projected.payload)

    assert result.error_code == "network_timeout"
    for secret in (
        "credential",
        "password",
        "private/path",
        "query-secret",
        "private-fragment",
    ):
        assert secret not in serialized


def test_manager_runs_local_conversation_through_native_arun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    learning_goal: LearningGoal,
) -> None:
    repository = SessionRepository()
    manager = ConversationManager(
        repository=repository,
        audit_log=InMemoryEventLog(),
        project_root=tmp_path,
        llm_factory=lambda session_id: TestLLM.from_messages([]),
    )
    manager.create("sess_arun", learning_goal)
    calls: list[str] = []

    def forbidden_run(self: LocalConversation) -> None:
        del self
        calls.append("run")
        raise AssertionError("synchronous run must not be used")

    async def recorded_arun(self: LocalConversation) -> None:
        del self
        calls.append("arun")

    monkeypatch.setattr(LocalConversation, "run", forbidden_run)
    monkeypatch.setattr(LocalConversation, "arun", recorded_arun)
    try:
        manager.run_review("sess_arun")
    finally:
        manager.close("sess_arun")

    assert calls == ["arun"]


def test_cancelled_arun_emits_native_interrupt_and_orphan_completion(
    tmp_path: Path,
    learning_goal: LearningGoal,
) -> None:
    started = Event()
    release = Event()

    class _BlockingFetcher:
        total_timeout_seconds = 5.0

        def fetch(self, source_url: str) -> FetchedUrl:
            del source_url
            started.set()
            release.wait(2.0)
            return FetchedUrl(
                final_url="https://example.com/",
                status_code=200,
                content_type="text/plain",
                content_length=2,
                redirect_chain=(),
                title=None,
                text_excerpt="ok",
            )

    repository = SessionRepository()
    repository.add_evidence(
        "sess_native_interrupt",
        _url_evidence("ev_native", "https://example.com/"),
    )
    call = MessageToolCall(
        id="call_native_interrupt",
        name="focusproof_url_evidence_verification",
        arguments='{"evidence_id":"ev_native"}',
        origin="completion",
    )
    factory = ConversationFactory(
        repository=repository,
        compatibility_mode=True,
        project_root=tmp_path,
        url_fetcher=_BlockingFetcher(),
        llm_factory=lambda session_id: TestLLM.from_messages(
            [
                Message(
                    role="assistant",
                    content=[TextContent(text="verify")],
                    tool_calls=[call],
                )
            ]
        ),
    )
    handle = factory.create(
        "sess_native_interrupt",
        learning_goal,
        evidence_types={"url"},
    )
    cast(Any, handle.conversation).send_message("Begin review")

    async def scenario() -> None:
        task = asyncio.ensure_future(
            cast(_AsyncRunnableConversation, handle.conversation).arun()
        )
        assert await asyncio.to_thread(started.wait, 1.0)
        handle.conversation.interrupt()
        await asyncio.wait_for(task, timeout=1.0)

    try:
        asyncio.run(scenario())
        events = list(handle.conversation.state.events)
    finally:
        release.set()
        handle.conversation.close()

    assert any(isinstance(event, InterruptEvent) for event in events)
    assert any(isinstance(event, AgentErrorEvent) for event in events)
