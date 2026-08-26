"""Behavioral RED tests for route-aware streaming request limits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI
from starlette.routing import Mount
from starlette.types import Message, Scope

from focusproof.api import app as app_module
from focusproof.api.app import RequestBodyLimitMiddleware
from focusproof.api.auth import get_verified_identity
from focusproof.api.oidc import InvalidTokenError
from focusproof.api.request_limits import (
    DEFAULT_BODY_LIMIT_BYTES,
    MEDIA_UPLOAD_BODY_LIMIT_BYTES,
    MEDIA_UPLOAD_ROUTE_NAME,
    BodyLimitResolver,
    MediaUploadRoute,
)


async def _endpoint() -> dict[str, bool]:
    return {"ok": True}


def _scope(
    path: str,
    *,
    method: str = "POST",
    root_path: str = "",
    headers: list[tuple[bytes, bytes]] | None = None,
    application: FastAPI | None = None,
) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": root_path,
        "query_string": b"",
        "headers": headers or [],
        "client": ("test", 1),
        "server": ("test", 80),
        "app": application,
    }


def _api_with_media_route(*, duplicates: int = 1) -> FastAPI:
    application = FastAPI()
    for _ in range(duplicates):
        application.router.add_api_route(
            "/sessions/{session_id}/evidence/image",
            _endpoint,
            methods=["POST"],
            name=MEDIA_UPLOAD_ROUTE_NAME,
            route_class_override=MediaUploadRoute,
        )
    return application


def test_unique_full_media_route_gets_media_limit() -> None:
    application = _api_with_media_route()
    resolver = BodyLimitResolver(application)

    assert (
        resolver.resolve(
            _scope("/sessions/sess-1/evidence/image", application=application)
        )
        == MEDIA_UPLOAD_BODY_LIMIT_BYTES
    )


def test_partial_or_method_mismatch_fails_closed() -> None:
    application = _api_with_media_route()
    resolver = BodyLimitResolver(application)

    assert resolver.resolve(_scope("/sessions/sess-1/evidence/image", method="GET")) == DEFAULT_BODY_LIMIT_BYTES
    assert resolver.resolve(_scope("/sessions/sess-1/evidence/image", method="PUT")) == DEFAULT_BODY_LIMIT_BYTES


def test_404_and_similar_substring_fail_closed() -> None:
    application = _api_with_media_route()
    resolver = BodyLimitResolver(application)

    assert resolver.resolve(_scope("/missing", application=application)) == DEFAULT_BODY_LIMIT_BYTES
    assert resolver.resolve(_scope("/prefix/sessions/s/evidence/image/suffix")) == DEFAULT_BODY_LIMIT_BYTES


def test_root_path_is_delegated_to_starlette_route_matching() -> None:
    application = _api_with_media_route()
    resolver = BodyLimitResolver(application)

    assert (
        resolver.resolve(
            _scope(
                "/prefix/sessions/s/evidence/image",
                root_path="/prefix",
                application=application,
            )
        )
        == MEDIA_UPLOAD_BODY_LIMIT_BYTES
    )


def test_duplicate_full_matches_are_ambiguous_and_fail_closed() -> None:
    application = _api_with_media_route(duplicates=2)

    assert (
        BodyLimitResolver(application).resolve(
            _scope("/sessions/s/evidence/image", application=application)
        )
        == DEFAULT_BODY_LIMIT_BYTES
    )


def test_mount_match_never_raises_limit() -> None:
    nested = _api_with_media_route()
    application = FastAPI()
    application.routes.append(Mount("/nested", app=nested))

    assert (
        BodyLimitResolver(application).resolve(
            _scope("/nested/sessions/s/evidence/image", application=application)
        )
        == DEFAULT_BODY_LIMIT_BYTES
    )


def _run_middleware(
    application: FastAPI,
    messages: list[Message],
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    path: str = "/ordinary",
    downstream: Callable[..., Any] | None = None,
) -> tuple[list[Message], list[str], int]:
    sent: list[Message] = []
    order: list[str] = []
    receive_calls = 0

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        order.append(f"source:{receive_calls}")
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    async def default_downstream(scope: Scope, inner_receive: Any, inner_send: Any) -> None:
        del scope
        while True:
            message = await inner_receive()
            order.append("downstream:received")
            if not message.get("more_body", False):
                break
        await inner_send({"type": "http.response.start", "status": 204, "headers": []})
        await inner_send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(
        downstream or default_downstream,
        resolver=BodyLimitResolver(application),
    )
    asyncio.run(
        middleware(
            _scope(path, headers=headers, application=application),
            receive,
            send,
        )
    )
    return sent, order, receive_calls


def test_content_length_is_rejected_before_receive() -> None:
    application = FastAPI()
    sent, _, calls = _run_middleware(
        application,
        [{"type": "http.request", "body": b"never", "more_body": False}],
        headers=[(b"content-length", str(DEFAULT_BODY_LIMIT_BYTES + 1).encode())],
    )

    assert calls == 0
    assert sent[0]["status"] == 413


def test_chunked_body_is_rejected_when_running_total_exceeds_limit() -> None:
    application = FastAPI()
    sent, _, calls = _run_middleware(
        application,
        [
            {"type": "http.request", "body": b"a" * DEFAULT_BODY_LIMIT_BYTES, "more_body": True},
            {"type": "http.request", "body": b"b", "more_body": False},
        ],
    )

    assert calls == 2
    assert sent[0]["status"] == 413


def test_receive_is_forwarded_incrementally_without_caching() -> None:
    application = FastAPI()
    original = [
        {"type": "http.request", "body": b"one", "more_body": True},
        {"type": "http.request", "body": b"two", "more_body": False},
    ]
    observed: list[Message] = []

    async def downstream(scope: Scope, receive: Any, send: Any) -> None:
        del scope

        observed.append(await receive())
        observed.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent, order, calls = _run_middleware(
        application,
        list(original),
        downstream=downstream,
    )

    assert calls == 2
    assert observed == original
    assert order == ["source:1", "source:2"]
    assert sent[0]["status"] == 204
def test_drain_detects_overflow_when_downstream_reads_only_first_chunk() -> None:
    application = FastAPI()

    async def downstream(scope: Scope, receive: Any, send: Any) -> None:
        del scope
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent, _, calls = _run_middleware(
        application,
        [
            {"type": "http.request", "body": b"a" * DEFAULT_BODY_LIMIT_BYTES, "more_body": True},
            {"type": "http.request", "body": b"b", "more_body": False},
        ],
        downstream=downstream,
    )

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert calls == 2
    assert [message["status"] for message in starts] == [413]


def test_early_downstream_start_is_not_flushed_before_overflow_decision() -> None:
    application = FastAPI()

    async def downstream(scope: Scope, receive: Any, send: Any) -> None:
        del scope
        await receive()
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await receive()
        await send({"type": "http.response.body", "body": b"accepted"})

    sent, _, _ = _run_middleware(
        application,
        [
            {"type": "http.request", "body": b"a" * DEFAULT_BODY_LIMIT_BYTES, "more_body": True},
            {"type": "http.request", "body": b"b", "more_body": False},
        ],
        downstream=downstream,
    )

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert [message["status"] for message in starts] == [413]


def test_disconnect_terminates_drain_without_duplicate_response() -> None:
    application = FastAPI()

    async def downstream(scope: Scope, receive: Any, send: Any) -> None:
        del scope
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent, _, calls = _run_middleware(
        application,
        [
            {"type": "http.request", "body": b"one", "more_body": True},
            {"type": "http.disconnect"},
        ],
        downstream=downstream,
    )

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert calls == 2
    assert [message["status"] for message in starts] == [204]



def test_unauthenticated_large_body_is_rejected_before_receive(monkeypatch: Any) -> None:
    application = FastAPI()

    async def protected(identity: Any = Depends(get_verified_identity)) -> None:
        del identity

    application.add_api_route("/protected", protected, methods=["POST"])

    async def reject_identity(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise InvalidTokenError()

    monkeypatch.setattr(app_module, "resolve_verified_identity", reject_identity)
    sent, _, calls = _run_middleware(
        application,
        [{"type": "http.request", "body": b"x", "more_body": False}],
        path="/protected",
        headers=[(b"content-length", str(DEFAULT_BODY_LIMIT_BYTES + 1).encode())],
    )

    assert calls == 0
    assert sent[0]["status"] == 401

def test_drain_stops_immediately_when_overflowing_chunk_has_more_body() -> None:
    application = FastAPI()

    async def downstream(scope: Scope, receive: Any, send: Any) -> None:
        del scope
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent, _, calls = _run_middleware(
        application,
        [
            {
                "type": "http.request",
                "body": b"a" * DEFAULT_BODY_LIMIT_BYTES,
                "more_body": True,
            },
            {"type": "http.request", "body": b"b", "more_body": True},
        ],
        downstream=downstream,
    )

    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert calls == 2
    assert [message["status"] for message in starts] == [413]
