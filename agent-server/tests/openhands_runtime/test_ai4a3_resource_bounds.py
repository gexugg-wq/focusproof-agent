from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from time import monotonic
from typing import TypeVar

import pytest

from focusproof.openhands_runtime.tools.url_execution import (
    BoundedUrlExecutionPool,
    UrlExecutionBusyError,
    UrlExecutionPoolClosedError,
)
from focusproof.openhands_runtime.tool_registry import (
    configure_repository_provider,
    configure_url_execution_pool_provider,
    configure_url_fetcher_provider,
    release_repository_provider,
)
from focusproof.openhands_runtime.tools.url_evidence import (
    UrlEvidenceVerificationExecutor,
)
from focusproof.openhands_runtime.tools.url_fetcher import FetchedUrl
from focusproof.openhands_runtime.tools.verification import EvidenceReferenceAction
from focusproof.runtime.evidence import Evidence


class _Repository:
    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        assert session_id == "sess_1"
        return Evidence(
            evidenceId=evidence_id,
            evidenceType="url",
            contentHash=f"sha256:{evidence_id}",
            sourceUrl="https://example.com/evidence",
        )


class _RecordingFetcher:
    def __init__(self) -> None:
        self._lock = Lock()
        self.calls = 0

    def fetch(self, source_url: str) -> FetchedUrl:
        del source_url
        with self._lock:
            self.calls += 1
        return FetchedUrl(
            final_url="https://example.com/evidence",
            status_code=200,
            content_type="text/plain",
            content_length=2,
            redirect_chain=(),
            title="",
            text_excerpt="ok",
        )


class _SecretUrlRepository:
    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        assert session_id == "sess_1"
        return Evidence(
            evidenceId=evidence_id,
            evidenceType="url",
            contentHash=f"sha256:{evidence_id}",
            sourceUrl=(
                "https://example.com/private/path-secret"
                "?token=query-secret#private-fragment"
            ),
        )


class _ImmediateTimeoutFetcher:
    total_timeout_seconds = 0.25

    def fetch(self, source_url: str) -> FetchedUrl:
        del source_url
        raise TimeoutError("internal-timeout-secret must never escape")


T = TypeVar("T")


class _GatePool(BoundedUrlExecutionPool):
    def __init__(self) -> None:
        super().__init__(max_workers=1, max_pending=1)
        self.submit_entered = Event()
        self.release_submit = Event()
        self._gate_next = False

    def gate_next_submit(self) -> None:
        self._gate_next = True

    def submit(self, operation: Callable[[], T]) -> Future[T]:
        if self._gate_next:
            self._gate_next = False
            self.submit_entered.set()
            assert self.release_submit.wait(2.0)
        return super().submit(operation)


def test_url_execution_pool_bounds_running_and_pending_work() -> None:
    release = Event()
    pool = BoundedUrlExecutionPool(max_workers=2, max_pending=1)
    try:
        futures = [pool.submit(lambda: release.wait(2.0)) for _ in range(3)]
        started = monotonic()
        with pytest.raises(UrlExecutionBusyError):
            pool.submit(lambda: None)
        assert monotonic() - started < 0.1
        assert pool.in_flight == 3
        assert pool.max_in_flight == 3
    finally:
        release.set()
        for future in locals().get("futures", []):
            future.result(timeout=1.0)
        pool.close()


def test_closed_url_execution_pool_rejects_without_submitting() -> None:
    pool = BoundedUrlExecutionPool(max_workers=1, max_pending=0)
    pool.close()

    with pytest.raises(UrlExecutionPoolClosedError):
        pool.submit(lambda: None)

    assert pool.submitted_count == 0
    assert pool.in_flight == 0


def test_url_execution_pool_recovers_capacity_after_completion() -> None:
    pool = BoundedUrlExecutionPool(max_workers=1, max_pending=0)
    try:
        assert pool.submit(lambda: "first").result(timeout=1.0) == "first"
        assert pool.in_flight == 0
        assert pool.submit(lambda: "second").result(timeout=1.0) == "second"
        assert pool.submitted_count == 2
    finally:
        pool.close()


def test_closed_url_executor_never_submits_fetch_work() -> None:
    pool = BoundedUrlExecutionPool(max_workers=1, max_pending=0)
    fetcher = _RecordingFetcher()
    executor = UrlEvidenceVerificationExecutor(
        _Repository(),
        "sess_1",
        fetcher,
        execution_pool=pool,
    )
    try:
        executor.close()
        result = executor(EvidenceReferenceAction(evidence_id="ev_closed"))

        assert result.status == "inconclusive"
        assert result.error_code == "verifier_closed"
        assert fetcher.calls == 0
        assert pool.submitted_count == 0
    finally:
        pool.close()


def test_executor_close_linearizes_with_submit_and_cancels_queued_fetch() -> None:
    worker_release = Event()
    pool = _GatePool()
    blocker = pool.submit(lambda: worker_release.wait(2.0))
    pool.gate_next_submit()
    fetcher = _RecordingFetcher()
    executor = UrlEvidenceVerificationExecutor(
        _Repository(),
        "sess_1",
        fetcher,
        execution_pool=pool,
    )
    callers = ThreadPoolExecutor(max_workers=2)
    try:
        call = callers.submit(
            executor,
            EvidenceReferenceAction(evidence_id="ev_race"),
        )
        assert pool.submit_entered.wait(1.0)
        close_returned = Event()

        def close_executor() -> None:
            executor.close()
            close_returned.set()

        closing = callers.submit(close_executor)
        returned_during_submit = close_returned.wait(0.05)
        pool.release_submit.set()
        closing.result(timeout=1.0)
        result = call.result(timeout=1.0)
        worker_release.set()
        blocker.result(timeout=1.0)

        assert returned_during_submit is False
        assert close_returned.is_set()
        assert result.status == "inconclusive"
        assert result.error_code == "network_timeout"
        assert fetcher.calls == 0
    finally:
        pool.release_submit.set()
        worker_release.set()
        callers.shutdown(wait=True, cancel_futures=True)
        pool.close()


def test_closed_execution_pool_maps_to_verifier_closed_not_busy() -> None:
    pool = BoundedUrlExecutionPool(max_workers=1, max_pending=0)
    fetcher = _RecordingFetcher()
    executor = UrlEvidenceVerificationExecutor(
        _Repository(),
        "sess_1",
        fetcher,
        execution_pool=pool,
    )
    pool.close()

    result = executor(EvidenceReferenceAction(evidence_id="ev_pool_closed"))

    assert result.status == "inconclusive"
    assert result.error_code == "verifier_closed"
    assert fetcher.calls == 0


def test_closed_executor_after_provider_release_returns_safe_observation() -> None:
    pool = BoundedUrlExecutionPool(max_workers=1, max_pending=0)
    fetcher = _RecordingFetcher()
    configure_repository_provider(_Repository())
    configure_url_fetcher_provider(fetcher)
    configure_url_execution_pool_provider(pool)
    executor = UrlEvidenceVerificationExecutor(None, "sess_1", None)
    executor.close()
    release_repository_provider()
    try:
        result = executor(EvidenceReferenceAction(evidence_id="ev_late"))

        assert result.status == "inconclusive"
        assert result.error_code == "verifier_closed"
        assert result.source_refs == ["ev_late"]
        assert fetcher.calls == 0
        assert pool.submitted_count == 0
    finally:
        pool.close()


def test_completed_future_timeout_error_maps_immediately_and_safely() -> None:
    pool = BoundedUrlExecutionPool(max_workers=1, max_pending=0)
    executor = UrlEvidenceVerificationExecutor(
        _SecretUrlRepository(),
        "sess_1",
        _ImmediateTimeoutFetcher(),
        execution_pool=pool,
    )
    try:
        started = monotonic()
        result = executor(EvidenceReferenceAction(evidence_id="ev_timeout"))
        elapsed = monotonic() - started
        serialized = result.model_dump_json()

        assert elapsed < 0.1
        assert result.status == "inconclusive"
        assert result.error_code == "network_timeout"
        for secret in (
            "internal-timeout-secret",
            "private/path-secret",
            "query-secret",
            "private-fragment",
        ):
            assert secret not in serialized
    finally:
        pool.close()
