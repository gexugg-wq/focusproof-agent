from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope

from focusproof.api.auth import VerifiedIdentity
from focusproof.api.oidc import InvalidTokenError
from focusproof.api.request_limits import BodyLimitResolver
from focusproof.api.speech_admission import (
    SpeechAdmissionGate,
    SpeechAdmissionMiddleware,
    SpeechTaskRegistry,
)
from focusproof.api.speech_routes import build_speech_router
from focusproof.persistence.repositories import (
    SpeechAdmissionError,
    SpeechAdmissionToken,
    SpeechQuotaExceededError,
)
from focusproof.speech_core.errors import SpeechErrorCode


@dataclass
class _Repo:
    failure: Exception | None = None
    calls: int = 0
    finalizations: list[tuple[str, str | None]] = field(default_factory=list)

    def admit(self, **values: Any) -> SpeechAdmissionToken:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        now = datetime.now(UTC)
        return SpeechAdmissionToken(
            request_id=str(uuid4()),
            owner_user_id=values["owner_user_id"],
            session_id=values["session_id"],
            lease_owner=values["lease_owner"],
            lease_generation=1,
            lease_expires_at=now + timedelta(seconds=120),
        )

    def finalize(
        self,
        token: SpeechAdmissionToken,
        *,
        state: str,
        outcome_code: str | None = None,
    ) -> bool:
        del token
        self.finalizations.append((state, outcome_code))
        return True


class _Uow:
    def __init__(self, repo: _Repo) -> None:
        self.speech_requests = repo
        self.committed = False

    def __enter__(self) -> _Uow:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def commit(self) -> None:
        self.committed = True


def _scope(
    application: FastAPI,
    *,
    headers: list[tuple[bytes, bytes]],
) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/sessions/sess-1/transcriptions",
        "raw_path": b"/sessions/sess-1/transcriptions",
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("test", 1),
        "server": ("test", 80),
        "app": application,
        "state": {},
    }


async def _run(
    *,
    repo: _Repo,
    identity_error: Exception | None = None,
    key: str | None = None,
    content_length: int | None = None,
    chunks: list[bytes] | None = None,
    gate: SpeechAdmissionGate | None = None,
    disconnect: bool = False,
    total_timeout_seconds: float = 120.0,
) -> tuple[list[Message], int, list[Scope]]:
    application = FastAPI()
    application.include_router(build_speech_router())
    application.state.uow_factory = lambda: _Uow(repo)
    application.state.speech_capability = {"enabled": True}
    registry = SpeechTaskRegistry()
    application.state.speech_task_registry = registry
    headers: list[tuple[bytes, bytes]] = [(b"authorization", b"Bearer token")]
    if key is not None:
        headers.append((b"idempotency-key", key.encode()))
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    receive_calls = 0
    source = list(chunks or [b"ok"])
    sent: list[Message] = []
    observed: list[Scope] = []

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        if disconnect:
            return {"type": "http.disconnect"}
        body = source.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(source)}

    async def send(message: Message) -> None:
        sent.append(message)

    async def downstream(scope: Scope, inner_receive: Any, inner_send: Any) -> None:
        observed.append(scope)
        scope["state"]["registry_active_during_receive"] = registry.active_count
        while True:
            message = await inner_receive()
            if message["type"] == "http.disconnect":
                return
            if not message.get("more_body", False):
                break
        await inner_send({"type": "http.response.start", "status": 204, "headers": []})
        await inner_send({"type": "http.response.body", "body": b""})

    async def identity_resolver(*args: Any, **kwargs: Any) -> VerifiedIdentity:
        del args, kwargs
        if identity_error is not None:
            raise identity_error
        return VerifiedIdentity(verified_user_id="user-1", token_fingerprint="fingerprint")

    limited = __import__(
        "focusproof.api.app", fromlist=["RequestBodyLimitMiddleware"]
    ).RequestBodyLimitMiddleware(
        downstream,
        resolver=BodyLimitResolver(application),
    )
    middleware = SpeechAdmissionMiddleware(
        limited,
        application=application,
        gate=gate or SpeechAdmissionGate(opened=True),
        identity_resolver=identity_resolver,
        clock=lambda: 40.0,
        total_timeout_seconds=total_timeout_seconds,
        cleanup_reserve_seconds=min(5.0, total_timeout_seconds / 2),
    )
    await middleware(_scope(application, headers=headers), receive, send)
    return sent, receive_calls, observed


@pytest.mark.anyio
async def test_invalid_token_is_rejected_before_any_multipart_receive() -> None:
    sent, receives, _ = await _run(
        repo=_Repo(),
        identity_error=InvalidTokenError(),
        key=str(uuid4()),
    )

    assert receives == 0
    assert sent[0]["status"] == 401


@pytest.mark.anyio
@pytest.mark.parametrize("key", [None, "not-a-uuid"])
async def test_missing_or_invalid_uuid_key_is_rejected_before_receive(
    key: str | None,
) -> None:
    repo = _Repo()
    sent, receives, _ = await _run(repo=repo, key=key)

    assert receives == 0
    assert repo.calls == 0
    assert sent[0]["status"] == 422


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (SpeechAdmissionError(SpeechErrorCode.TRANSCRIPTION_FAILED), 404),
        (SpeechQuotaExceededError("private quota"), 429),
    ],
)
async def test_non_owner_and_quota_rejection_read_zero_bytes(
    failure: Exception,
    status: int,
) -> None:
    sent, receives, _ = await _run(
        repo=_Repo(failure=failure),
        key=str(uuid4()),
    )

    assert receives == 0
    assert sent[0]["status"] == status


@pytest.mark.anyio
async def test_declared_overflow_is_admitted_then_rejected_without_receive() -> None:
    repo = _Repo()
    sent, receives, observed = await _run(
        repo=repo,
        key=str(uuid4()),
        content_length=11 * 1024 * 1024 + 1,
    )

    assert repo.calls == 1
    assert receives == 0
    assert observed == []
    assert sent[0]["status"] == 413


@pytest.mark.anyio
async def test_chunked_total_over_11_mib_is_streamed_then_rejected() -> None:
    sent, receives, observed = await _run(
        repo=_Repo(),
        key=str(uuid4()),
        chunks=[b"a" * (11 * 1024 * 1024), b"b"],
    )

    assert receives == 2
    assert len(observed) == 1
    assert sent[0]["status"] == 413


@pytest.mark.anyio
async def test_scope_receives_immutable_token_and_one_entry_deadline() -> None:
    key = str(uuid4())
    sent, _, observed = await _run(repo=_Repo(), key=key)

    assert sent[0]["status"] == 204
    admission = observed[0]["state"]["speech_admission"]
    UUID(admission.token.request_id)
    assert admission.token.owner_user_id == "user-1"
    assert admission.token.session_id == "sess-1"
    assert admission.deadline == 160.0
    assert observed[0]["state"]["registry_active_during_receive"] == 1
    with pytest.raises((AttributeError, TypeError)):
        admission.deadline = 999.0


@pytest.mark.anyio
async def test_duplicate_result_unavailable_uses_shared_410_mapping() -> None:
    sent, receives, _ = await _run(
        repo=_Repo(
            failure=SpeechAdmissionError(
                SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE
            )
        ),
        key=str(uuid4()),
    )

    assert receives == 0
    assert sent[0]["status"] == 410


@pytest.mark.anyio
async def test_pre_parser_disconnect_is_registered_and_finalizes_admission() -> None:
    repo = _Repo()
    sent, receives, observed = await _run(
        repo=repo,
        key=str(uuid4()),
        disconnect=True,
        total_timeout_seconds=0.05,
    )

    assert receives == 1
    assert observed[0]["state"]["registry_active_during_receive"] == 1
    assert repo.finalizations == [("cancelled", "client_cancelled")]
    assert sent == []


@pytest.mark.anyio
async def test_pre_parser_slow_receive_uses_business_deadline_and_cleanup_reserve() -> None:
    application = FastAPI()
    application.include_router(build_speech_router())
    repo = _Repo()
    application.state.uow_factory = lambda: _Uow(repo)
    application.state.speech_capability = {"enabled": True}
    registry = SpeechTaskRegistry()
    application.state.speech_task_registry = registry
    observed: list[int] = []

    async def receive() -> Message:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def send(message: Message) -> None:
        del message

    async def downstream(scope: Scope, inner_receive: Any, inner_send: Any) -> None:
        del scope, inner_send
        observed.append(registry.active_count)
        await inner_receive()

    async def identity_resolver(*args: Any, **kwargs: Any) -> VerifiedIdentity:
        del args, kwargs
        return VerifiedIdentity(
            verified_user_id="user-1", token_fingerprint="fingerprint"
        )

    middleware = SpeechAdmissionMiddleware(
        downstream,
        application=application,
        gate=SpeechAdmissionGate(opened=True),
        identity_resolver=identity_resolver,
        total_timeout_seconds=0.05,
        cleanup_reserve_seconds=0.02,
    )
    headers = [
        (b"authorization", b"Bearer token"),
        (b"idempotency-key", str(uuid4()).encode()),
    ]

    await asyncio.wait_for(
        middleware(_scope(application, headers=headers), receive, send),
        timeout=0.2,
    )

    assert observed == [1]
    assert repo.finalizations == [("failed_terminal", "transcription_timeout")]
    assert registry.active_count == 0
