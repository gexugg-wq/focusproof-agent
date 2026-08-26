"""Behavioral RED tests for the opt-in image evidence endpoint."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
import subprocess
import sys
import base64
from io import BytesIO
from dataclasses import dataclass
from threading import Event
from types import SimpleNamespace
from typing import Any, Callable, ContextManager, Iterator, Literal, Protocol, cast
from pathlib import Path

from alembic import command as alembic_command
from httpx import ASGITransport, AsyncClient
from openhands.sdk.event import MessageEvent
from alembic.config import Config
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from sqlalchemy import text
from sqlalchemy.engine import make_url

import pytest
from focusproof.api import auth as auth_module
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from focusproof.api.auth import VerifiedIdentity
from fastapi.testclient import TestClient

from focusproof.api import app as app_module
from focusproof.api.auth import get_verified_identity
from focusproof.api.media_models import MediaEvidenceResponse
from focusproof.api.media_routes import (
    build_media_router,
)
from focusproof.media_core.models import (
    FinalizeMediaOutcome,
    FinalizeMediaRequest,
    IngestedEvidenceResult,
)
from focusproof.media_application import (
    MediaDisabledError,
    MediaMaliciousError,
    MediaScanUnavailableError,
    MediaSourceTooLargeError,
    UnsupportedMediaError,
)
from focusproof.openhands_runtime.locks import (
    FileSessionRunLock,
    SessionBusyError,
    SessionRunLock,
)
from focusproof.openhands_runtime.synchronizer import message_key_from_event
from focusproof.persistence.providers import UowEvidenceProvider
from focusproof.persistence.repositories import (
    IdempotencyConflictError,
    MediaAuthorizationError,
    MediaQuotaExceededError,
    SqlMediaTransactionRepository,
)
from focusproof.runtime.evidence import Evidence, LearningGoal

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _ClosableStream(Protocol):
    @property
    def closed(self) -> bool: ...


class _CancellationGate(Protocol):
    def run_commit(self, action: Callable[[], object]) -> object: ...


class _ReviewManager(Protocol):
    _run_lock: SessionRunLock

    def _load_scoring_facts(
        self,
        session_id: str,
    ) -> tuple[LearningGoal, list[Evidence], list[str]]: ...


class _ObservedSessionRunLock:
    def __init__(self, acquire: Callable[[str], ContextManager[None]]) -> None:
        self._acquire = acquire

    def acquire(self, session_id: str) -> ContextManager[None]:
        return self._acquire(session_id)


@dataclass
class FakeCommand:
    outcome: object
    calls: list[dict[str, object]]

    def execute(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        stream = kwargs["stream"]
        assert hasattr(stream, "read")
        assert not isinstance(stream, bytes)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _result(*, replayed: bool = False) -> object:
    core = IngestedEvidenceResult(
        evidence_id="ev_safe",
        media_item_id="media_internal",
        artifact_ref="objects/private/secret.png",
        media_type="image/png",
        normalized_sha256="secret-hash",
        byte_size=67,
        learner_explanation="explain it",
        attributes={"width": 1, "height": 1},
    )
    return SimpleNamespace(result=core, replayed=replayed)


def _client(command: FakeCommand) -> TestClient:
    application = FastAPI()
    application.state.allow_anonymous_identity = True
    application.include_router(build_media_router(command))
    return TestClient(application, raise_server_exceptions=False)


def _post(
    client: TestClient, *, explanation: str = "  explanation  ", data: bytes = PNG_1X1
) -> Any:
    return client.post(
        "/sessions/sess-1/evidence/image",
        files={"file": ("image.png", data, "image/png")},
        data={"explanation": explanation, "idempotency_key": "idem-1"},
    )


@pytest.mark.parametrize("key", ["x" * 256, "bad\nkey", "space key"])
def test_image_idempotency_key_rejects_unsafe_values_before_command(key: str) -> None:
    command = FakeCommand(_result(), [])
    with _client(command) as client:
        response = client.post(
            "/sessions/sess-1/evidence/image",
            files={"file": ("image.png", PNG_1X1, "image/png")},
            data={"explanation": "explanation", "idempotency_key": key},
        )
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_idempotency_key", "retryable": False}
    assert command.calls == []


def test_image_idempotency_key_accepts_255_safe_characters() -> None:
    command = FakeCommand(_result(), [])
    with _client(command) as client:
        response = client.post(
            "/sessions/sess-1/evidence/image",
            files={"file": ("image.png", PNG_1X1, "image/png")},
            data={"explanation": "explanation", "idempotency_key": "x" * 255},
        )
    assert response.status_code == 200

def test_disabled_create_app_has_no_image_route(monkeypatch: Any) -> None:
    monkeypatch.setenv("FOCUSPROOF_MEDIA_ENABLED", "false")
    application = app_module.create_app()

    paths = {getattr(route, "path", None) for route in application.routes}
    assert "/sessions/{session_id}/evidence/image" not in paths


def test_enabled_multipart_success_maps_only_safe_fields_and_uses_one_worker_crossing(
    monkeypatch: Any,
) -> None:
    command = FakeCommand(_result(), [])
    client = _client(command)
    calls = 0

    async def immediate_to_thread(function: Any, /, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)
    response = _post(client)

    assert response.status_code == 200
    assert response.json() == {
        "evidenceId": "ev_safe",
        "mediaType": "image/png",
        "normalizedBytes": 67,
        "replayed": False,
    }
    assert set(MediaEvidenceResponse.model_fields) == set(response.json())
    assert calls == 1
    assert command.calls[0]["owner_id"] == "dev-anonymous-user"
    assert command.calls[0]["explanation"] == "explanation"


def test_unauthenticated_request_never_executes_command() -> None:
    command = FakeCommand(_result(), [])
    application = FastAPI()
    application.include_router(build_media_router(command))

    async def reject() -> None:
        raise HTTPException(status_code=401, detail="invalid token")

    application.dependency_overrides[get_verified_identity] = reject
    response = _post(TestClient(application, raise_server_exceptions=False))

    assert response.status_code == 401
    assert command.calls == []


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (MediaAuthorizationError("private session"), 404, "media_session_unavailable"),
        (UnsupportedMediaError("decoder /private/path object-key"), 415, "unsupported_media"),
        (MediaSourceTooLargeError("limit at /private/path"), 413, "media_too_large"),
        (IdempotencyConflictError("fingerprint secret"), 409, "idempotency_conflict"),
        (MediaQuotaExceededError("quota internals"), 409, "media_quota_exceeded"),
    ],
)
def test_domain_failures_are_safely_mapped(
    failure: Exception,
    status: int,
    code: str,
) -> None:
    command = FakeCommand(failure, [])
    response = _post(_client(command))

    assert response.status_code == status
    assert response.json() == {"code": code, "retryable": False}
    assert "/private" not in response.text
    assert "secret" not in response.text
    assert "object-key" not in response.text


def test_blank_trimmed_explanation_is_rejected_before_command() -> None:
    command = FakeCommand(_result(), [])
    response = _post(_client(command), explanation=" \t\r\n ")

    assert response.status_code == 422
    assert command.calls == []


def test_enabled_create_app_composes_real_image_ingestion(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'enabled-media.sqlite3'}"
    config = Config(project_root / "alembic.ini")
    config.set_main_option("script_location", str(project_root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(config, "head")
    monkeypatch.setenv("FOCUSPROOF_MEDIA_ENABLED", "true")
    monkeypatch.setenv("FOCUSPROOF_MEDIA_SCANNER_MODE", "fake-clean")
    application = app_module.create_app(
        database_url=database_url,
        data_dir=tmp_path,
        llm_factory=_draft_only_llm,
    )

    with TestClient(application) as client:
        created = client.post(
            "/sessions",
            json={
                "domain": "general",
                "title": "Image evidence",
                "goal": "Explain a pixel",
                "expectedOutput": "summary",
                "plannedMinutes": 5,
            },
        )
        assert created.status_code == 200
        session_id = created.json()["sessionId"]
        response = client.post(
            f"/sessions/{session_id}/evidence/image",
            files={"file": ("pixel.png", PNG_1X1, "image/png")},
            data={"explanation": "A single pixel.", "idempotency_key": "real-1"},
        )

        assert response.status_code == 200
        assert response.json()["mediaType"] == "image/png"
        assert response.json()["normalizedBytes"] > 0
        state = client.get(f"/sessions/{session_id}").json()
        capability = state["view"]["productCapabilities"][0]
        assert capability["capabilityId"] == "image_evidence"
        assert capability["maxCount"] == 4
        assert capability["maxOriginalBytes"] == 10485760
        assert capability["explanationRequired"] is True


def test_uploaded_image_tool_facts_come_from_authoritative_media_artifact(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    application = _real_app(monkeypatch, tmp_path)
    with TestClient(application) as client:
        session_id = _real_session(client)
        uploaded = client.post(
            f"/sessions/{session_id}/evidence/image",
            files={"file": ("pixel.png", PNG_1X1, "image/png")},
            data={"explanation": "A single verified pixel.", "idempotency_key": "tool-facts-1"},
        )
        assert uploaded.status_code == 200
        evidence_id = str(uploaded.json()["evidenceId"])
        with application.state.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE evidence SET metadata_json = :metadata WHERE evidence_id = :evidence_id"
                ),
                {
                    "evidence_id": evidence_id,
                    "metadata": '{"width":999,"height":777}',
                },
            )
        from focusproof.bootstrap.media_composition import (
            compose_media_message_content_provider,
        )

        content_provider = compose_media_message_content_provider(
            uow_factory=application.state.uow_factory,
            data_dir=tmp_path,
        )
        facts = (
            UowEvidenceProvider(
                application.state.uow_factory,
                content_provider,
            )
            .scope(session_id, "dev-anonymous-user")
            .get_media_evidence_facts(
                session_id,
                evidence_id,
            )
        )

    assert facts.evidence_id == evidence_id
    assert facts.media_type == "image/png"
    assert facts.byte_size == uploaded.json()["normalizedBytes"]
    assert facts.width == 1
    assert facts.height == 1


def test_idempotent_replay_is_disclosed_without_internal_identity() -> None:
    command = FakeCommand(_result(replayed=True), [])
    response = _post(_client(command))

    assert response.status_code == 200
    assert response.json()["replayed"] is True
    assert "mediaItemId" not in response.json()
    assert "artifactRef" not in response.json()
    assert "normalizedSha256" not in response.json()


def test_upload_file_is_closed_after_failed_request_cleanup() -> None:
    command = FakeCommand(UnsupportedMediaError("bad"), [])
    response = _post(_client(command))

    assert response.status_code == 415
    captured_stream = cast(_ClosableStream, command.calls[0]["stream"])
    assert captured_stream.closed is True


def test_unexpected_provider_error_is_safe() -> None:
    command = FakeCommand(
        RuntimeError("provider=/private/store object-key=opaque-secret"),
        [],
    )
    response = _post(_client(command))

    assert response.status_code == 500
    assert response.json() == {"code": "media_ingestion_failed", "retryable": True}
    assert "private" not in response.text
    assert "opaque-secret" not in response.text


def _draft_only_llm(session_id: str) -> TestLLM:
    del session_id
    draft = MessageToolCall(
        id="call_media_barrier_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Review only committed evidence."],
                "understanding_findings": ["The current facts were reviewed."],
                "contradictions": [],
                "recommended_next_step": "Verify any image evidence.",
                "confidence": 0.7,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Draft")], tool_calls=[draft])]
    )


def _real_app(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    llm_factory: Callable[[str], TestLLM] | None = None,
    database_url: str | None = None,
) -> FastAPI:
    project_root = Path(__file__).resolve().parents[3]
    resolved_database_url = database_url or (
        f"sqlite+pysqlite:///{tmp_path / 'media-contract.sqlite3'}"
    )
    config = Config(project_root / "alembic.ini")
    config.set_main_option("script_location", str(project_root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", resolved_database_url)
    alembic_command.upgrade(config, "head")
    monkeypatch.setenv("FOCUSPROOF_MEDIA_ENABLED", "true")
    monkeypatch.setenv("FOCUSPROOF_MEDIA_SCANNER_MODE", "fake-clean")
    return app_module.create_app(
        database_url=resolved_database_url,
        data_dir=tmp_path,
        llm_factory=llm_factory or _draft_only_llm,
    )


def _real_session(client: TestClient, headers: dict[str, str] | None = None) -> str:
    response = client.post(
        "/sessions",
        headers=headers,
        json={
            "domain": "general",
            "title": "Image evidence",
            "goal": "Explain a pixel",
            "expectedOutput": "summary",
            "plannedMinutes": 5,
        },
    )
    assert response.status_code == 200
    return str(response.json()["sessionId"])


@pytest.fixture
def postgres_barrier_url() -> str:
    raw = os.environ.get("FOCUSPROOF_TEST_POSTGRES_BARRIER_URL")
    if not raw:
        pytest.skip("FOCUSPROOF_TEST_POSTGRES_BARRIER_URL is not set")
    parsed = make_url(raw)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.database is not None
    assert parsed.database.startswith("focusproof_test_")
    return raw


def _assert_publish_review_barrier(
    publish_application: FastAPI,
    review_application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    lock_root: Path,
    *,
    session_id: str | None = None,
    review_lock_mode: Literal["delegate", "bypass"] = "delegate",
) -> None:
    finalize_entered = Event()
    allow_finalize_return = Event()
    review_lock_attempted = Event()
    review_lock_acquired = Event()
    scoring_facts_loaded = Event()
    original_finalize = SqlMediaTransactionRepository.finalize
    file_lock_acquire = FileSessionRunLock.acquire

    def finalize_with_barrier(
        self: SqlMediaTransactionRepository,
        request: FinalizeMediaRequest,
    ) -> FinalizeMediaOutcome:
        outcome = original_finalize(self, request)
        finalize_entered.set()
        assert allow_finalize_return.wait(timeout=15)
        return outcome

    monkeypatch.setattr(
        SqlMediaTransactionRepository,
        "finalize",
        finalize_with_barrier,
    )
    with TestClient(publish_application) as client:
        active_session_id = session_id or _real_session(client)
        with (
            TestClient(review_application) as review_client,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            manager = cast(
                _ReviewManager,
                review_application.state.conversation_manager,
            )
            original_load_scoring_facts = manager._load_scoring_facts
            original_review_lock = manager._run_lock

            def load_scoring_facts_with_barrier(
                scoring_session_id: str,
            ) -> tuple[LearningGoal, list[Evidence], list[str]]:
                scoring_facts_loaded.set()
                return original_load_scoring_facts(scoring_session_id)

            monkeypatch.setattr(
                manager,
                "_load_scoring_facts",
                load_scoring_facts_with_barrier,
            )

            @contextmanager
            def observed_review_acquire(
                locked_session_id: str,
            ) -> Iterator[None]:
                review_lock_attempted.set()
                if review_lock_mode == "bypass":
                    review_lock_acquired.set()
                    yield
                    return
                with original_review_lock.acquire(locked_session_id):
                    review_lock_acquired.set()
                    yield

            @contextmanager
            def allow_overlapping_write_route(_data_dir: Path) -> Iterator[None]:
                yield

            upload = pool.submit(
                client.post,
                f"/sessions/{active_session_id}/evidence/image",
                files={"file": ("pixel.png", PNG_1X1, "image/png")},
                data={
                    "explanation": "A newly published pixel.",
                    "idempotency_key": f"barrier-{active_session_id}",
                },
            )
            assert finalize_entered.wait(timeout=5)
            monkeypatch.setattr(app_module, "writer_barrier", allow_overlapping_write_route)
            monkeypatch.setattr(
                manager,
                "_run_lock",
                _ObservedSessionRunLock(observed_review_acquire),
            )
            try:
                publish_lock_contended = False
                try:
                    probe_lock = FileSessionRunLock(lock_root, timeout_seconds=0)
                    with file_lock_acquire(probe_lock, active_session_id):
                        pass
                except SessionBusyError:
                    publish_lock_contended = True
                review = pool.submit(
                    review_client.post,
                    f"/sessions/{active_session_id}/review",
                )
                assert review_lock_attempted.wait(timeout=5)
                assert not review_lock_acquired.is_set(), (
                    "review entered the session critical section before publish completed"
                )
                assert not scoring_facts_loaded.is_set(), (
                    "review loaded scoring facts while publish was still uncommitted"
                )
                assert not review.done(), "review returned before publish completed"
            finally:
                allow_finalize_return.set()
            upload_response = upload.result(timeout=10)
            review_response = review.result(timeout=10)

    assert upload_response.status_code == 200
    assert publish_lock_contended, (
        "media publish did not hold the production session lock before UoW commit"
    )
    assert review_response.status_code == 503, review_response.text
    assert review_response.json()["reviewStatus"] == "failed"
    assert "three verified native visual facts" in review_response.json()["error"]
    with publish_application.state.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM evidence WHERE session_id = :session_id"),
                {"session_id": active_session_id},
            )
            == 1
        )


def test_same_session_image_publish_and_review_are_linearized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    postgres_barrier_url: str,
) -> None:
    publish_application = _real_app(
        monkeypatch,
        tmp_path,
        llm_factory=_draft_only_llm,
        database_url=postgres_barrier_url,
    )
    review_application = _real_app(
        monkeypatch,
        tmp_path,
        llm_factory=_draft_only_llm,
        database_url=postgres_barrier_url,
    )
    _assert_publish_review_barrier(
        publish_application,
        review_application,
        monkeypatch,
        tmp_path,
    )


def test_concurrency_oracle_rejects_review_lock_bypass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    postgres_barrier_url: str,
) -> None:
    publish_application = _real_app(
        monkeypatch,
        tmp_path,
        llm_factory=_draft_only_llm,
        database_url=postgres_barrier_url,
    )
    review_application = _real_app(
        monkeypatch,
        tmp_path,
        llm_factory=_draft_only_llm,
        database_url=postgres_barrier_url,
    )
    with pytest.raises(AssertionError) as oracle_failure:
        _assert_publish_review_barrier(
            publish_application,
            review_application,
            monkeypatch,
            tmp_path,
            review_lock_mode="bypass",
        )
    assert str(oracle_failure.value).splitlines()[0] == (
        "review entered the session critical section before publish completed"
    )


def test_restart_then_immediate_image_upload_and_review_are_linearized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    postgres_barrier_url: str,
) -> None:
    first_application = _real_app(
        monkeypatch,
        tmp_path,
        llm_factory=_draft_only_llm,
        database_url=postgres_barrier_url,
    )
    with TestClient(first_application) as first_client:
        session_id = _real_session(first_client)

    restarted_publish_application = _real_app(
        monkeypatch,
        tmp_path,
        llm_factory=_draft_only_llm,
        database_url=postgres_barrier_url,
    )
    restarted_review_application = _real_app(
        monkeypatch,
        tmp_path,
        llm_factory=_draft_only_llm,
        database_url=postgres_barrier_url,
    )
    _assert_publish_review_barrier(
        restarted_publish_application,
        restarted_review_application,
        monkeypatch,
        tmp_path,
        session_id=session_id,
    )


def test_real_route_replays_same_idempotency_result(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    application = _real_app(monkeypatch, tmp_path)
    with TestClient(application) as client:
        session_id = _real_session(client)
        first = client.post(
            f"/sessions/{session_id}/evidence/image",
            files={"file": ("pixel.png", PNG_1X1, "image/png")},
            data={"explanation": "A pixel.", "idempotency_key": "replay-1"},
        )
        second = client.post(
            f"/sessions/{session_id}/evidence/image",
            files={"file": ("pixel.png", PNG_1X1, "image/png")},
            data={"explanation": "A pixel.", "idempotency_key": "replay-1"},
        )

        assert first.status_code == second.status_code == 200
        assert second.json()["evidenceId"] == first.json()["evidenceId"]
        assert first.json()["replayed"] is False
        assert second.json()["replayed"] is True


def test_concurrent_same_key_has_one_persistent_and_native_side_effect(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    application = _real_app(monkeypatch, tmp_path)
    with TestClient(application) as bootstrap:
        session_id = _real_session(bootstrap)

        async def race() -> list[Any]:
            gate = asyncio.Event()
            transport = ASGITransport(app=application)

            async def post() -> Any:
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    await gate.wait()
                    return await client.post(
                        f"/sessions/{session_id}/evidence/image",
                        files={"file": ("pixel.png", PNG_1X1, "image/png")},
                        data={"explanation": "One concurrent pixel.", "idempotency_key": "concurrent-same-1"},
                    )

            first = asyncio.create_task(post())
            second = asyncio.create_task(post())
            gate.set()
            return list(await asyncio.gather(first, second))

        responses = asyncio.run(race())
        assert [response.status_code for response in responses] == [200, 200]
        evidence_ids = {response.json()["evidenceId"] for response in responses}
        assert len(evidence_ids) == 1
        evidence_id = evidence_ids.pop()

        manager = application.state.conversation_manager
        manager.send_evidence(session_id, "dev-anonymous-user")
        manager.send_evidence(session_id, "dev-anonymous-user")
        handle = manager.get_or_restore(session_id, "dev-anonymous-user")
        messages = [
            event for event in handle.conversation.state.events
            if isinstance(event, MessageEvent) and message_key_from_event(event) == f"evidence:{evidence_id}"
        ]
        assert len(messages) == 1
        manager.run_review(session_id, "dev-anonymous-user")

        with application.state.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM evidence WHERE session_id = :session_id"), {"session_id": session_id}) == 1
            assert connection.scalar(text("SELECT count(*) FROM media_ingestion_reservations WHERE session_id = :session_id"), {"session_id": session_id}) == 1
            assert connection.scalar(text("SELECT count(*) FROM scan_attempts")) == 1
            assert connection.scalar(text("SELECT count(*) FROM clean_receipts")) == 1
            assert connection.scalar(text("SELECT count(*) FROM audit_events WHERE session_id = :session_id AND type = 'evidence.submitted'"), {"session_id": session_id}) == 1


def test_real_route_hides_session_from_other_owner_without_artifact(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    async def identity(request: Any, *, authorization: str | None = None) -> VerifiedIdentity:
        token = authorization or request.headers.get("authorization") or ""
        return VerifiedIdentity(verified_user_id=token.removeprefix("Bearer "))

    monkeypatch.setattr(app_module, "resolve_verified_identity", identity)
    monkeypatch.setattr(auth_module, "resolve_verified_identity", identity)
    application = _real_app(monkeypatch, tmp_path)
    with TestClient(application) as client:
        session_id = _real_session(client, headers={"Authorization": "Bearer owner-a"})
        response = client.post(
            f"/sessions/{session_id}/evidence/image",
            headers={"Authorization": "Bearer owner-b"},
            files={"file": ("pixel.png", PNG_1X1, "image/png")},
            data={"explanation": "A pixel.", "idempotency_key": "owner-b-1"},
        )

        assert response.status_code == 404
        with application.state.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM media_artifacts")) == 0


def test_real_route_fifth_image_hits_quota_without_residue(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    application = _real_app(monkeypatch, tmp_path)
    with TestClient(application) as client:
        session_id = _real_session(client)
        responses = [
            client.post(
                f"/sessions/{session_id}/evidence/image",
                files={"file": ("pixel.png", PNG_1X1, "image/png")},
                data={
                    "explanation": f"Pixel {index}.",
                    "idempotency_key": f"quota-{index}",
                },
            )
            for index in range(5)
        ]

        assert [item.status_code for item in responses] == [200, 200, 200, 200, 409]
        with application.state.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM evidence")) == 4
            assert connection.scalar(text("SELECT count(*) FROM media_artifacts")) == 1
        quarantine = tmp_path / "media" / "quarantine"
        assert len(tuple((quarantine / "payloads").glob("[!.]*"))) == 4
        assert len(tuple((quarantine / "records").glob("*.json"))) == 4
        assert len(tuple((quarantine / "commits").glob("*.commit"))) == 4
        assert tuple((quarantine / "untrusted-scan-spool").iterdir()) == ()
        assert not (quarantine / ("promotion" + "-claims")).exists()
        assert not any((tmp_path / "media" / "objects" / "staged").iterdir())
        assert not any((tmp_path / "media" / "objects" / "manifests").iterdir())


def test_real_corrupt_image_cleans_all_temporary_objects(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    application = _real_app(monkeypatch, tmp_path)
    with TestClient(application) as client:
        session_id = _real_session(client)
        response = client.post(
            f"/sessions/{session_id}/evidence/image",
            files={"file": ("broken.png", b"not an image", "image/png")},
            data={"explanation": "Broken.", "idempotency_key": "broken-1"},
        )

        assert response.status_code == 415
        with application.state.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM evidence")) == 0
            assert connection.scalar(text("SELECT count(*) FROM media_artifacts")) == 0
        quarantine = tmp_path / "media" / "quarantine"
        assert len(tuple((quarantine / "payloads").glob("[!.]*"))) == 1
        assert len(tuple((quarantine / "records").glob("*.json"))) == 1
        assert len(tuple((quarantine / "commits").glob("*.commit"))) == 1
        assert tuple((quarantine / "untrusted-scan-spool").iterdir()) == ()
        assert not (quarantine / ("promotion" + "-claims")).exists()
        for directory in (
            tmp_path / "media" / "objects" / "staged",
            tmp_path / "media" / "objects" / "manifests",
            tmp_path / "media" / "objects" / "referenced",
        ):
            assert not any(directory.iterdir())


def test_bootstrap_import_does_not_depend_on_api_delivery() -> None:
    project_root = Path(__file__).resolve().parents[3]
    code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith("focusproof.api"):
        raise RuntimeError("bootstrap imported API delivery")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import focusproof.bootstrap.media_composition
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_unknown_value_error_is_not_misclassified_as_unsupported_media() -> None:
    command = FakeCommand(ValueError("internal invariant /private/object-key"), [])
    response = _post(_client(command))

    assert response.status_code == 500
    assert response.json() == {"code": "media_ingestion_failed", "retryable": True}
    assert "private" not in response.text
    assert "object-key" not in response.text


@pytest.mark.parametrize(
    ("failure", "status", "code", "retryable"),
    [
        (MediaMaliciousError("EICAR /secret signature"), 422, "media_malicious", False),
        (MediaScanUnavailableError("tcp://secret raw FOUND"), 503, "media_scan_unavailable", True),
        (MediaDisabledError("/secret disabled"), 503, "media_disabled", False),
    ],
)
def test_scan_failures_have_stable_sanitized_public_mapping(
    failure: Exception, status: int, code: str, retryable: bool
) -> None:
    response = _post(_client(FakeCommand(failure, [])))
    assert response.status_code == status
    assert response.json() == {"code": code, "retryable": retryable}
    assert "secret" not in response.text
    assert "EICAR" not in response.text
    assert "FOUND" not in response.text


def test_cancelled_upload_waits_for_worker_and_blocks_commit() -> None:
    async def scenario() -> None:
        import threading
        from starlette.datastructures import UploadFile
        from starlette.requests import Request

        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        facts = {"stage": 0, "finalize": 0, "confirm": 0}

        class BlockingCommand:
            def execute(self, **kwargs: object) -> object:
                started.set()
                release.wait(timeout=2)
                gate = cast(_CancellationGate | None, kwargs.get("cancellation_gate"))
                try:
                    if gate is None:
                        facts.update(stage=1, finalize=1, confirm=1)
                    else:
                        gate.run_commit(lambda: facts.update(stage=1, finalize=1, confirm=1))
                    return _result()
                finally:
                    finished.set()

        router = build_media_router(BlockingCommand())
        route = next(
            route
            for route in router.routes
            if isinstance(route, APIRoute) and route.name == "upload_image_evidence"
        )
        endpoint = route.endpoint
        application = FastAPI()
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/sessions/session/evidence/image",
                "headers": [],
                "app": application,
            }
        )
        upload = UploadFile(filename="image.png", file=BytesIO(PNG_1X1))
        task = asyncio.create_task(
            endpoint(
                session_id="session",
                request=request,
                file=upload,
                explanation="evidence",
                idempotency_key="idem",
                identity=VerifiedIdentity(verified_user_id="owner"),
            )
        )
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(finished.wait, 1)
        assert facts == {"stage": 0, "finalize": 0, "confirm": 0}

    asyncio.run(scenario())
