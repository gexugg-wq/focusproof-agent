from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope


DEFAULT_BODY_LIMIT_BYTES = 256 * 1024
MEDIA_UPLOAD_BODY_LIMIT_BYTES = 11 * 1024 * 1024
MEDIA_UPLOAD_ROUTE_NAME = "upload_image_evidence"
SPEECH_UPLOAD_BODY_LIMIT_BYTES = 11 * 1024 * 1024
SPEECH_UPLOAD_ROUTE_NAME = "create_speech_transcription"
_MEDIA_ROUTE_MARKER = object()
_SPEECH_ROUTE_MARKER = object()


class MediaUploadRoute(APIRoute):
    _focusproof_media_route_marker = _MEDIA_ROUTE_MARKER


class SpeechUploadRoute(APIRoute):
    _focusproof_speech_route_marker = _SPEECH_ROUTE_MARKER


class BodyLimitResolver:
    def __init__(self, application: FastAPI) -> None:
        self._application = application

    def resolve(self, scope: Scope) -> int:
        full_matches = [route for route, _ in _full_api_route_matches(self._application, scope)]
        if len(full_matches) != 1:
            return DEFAULT_BODY_LIMIT_BYTES
        route = full_matches[0]
        if (
            route.name == MEDIA_UPLOAD_ROUTE_NAME
            and isinstance(route, MediaUploadRoute)
            and getattr(route, "_focusproof_media_route_marker", None)
            is _MEDIA_ROUTE_MARKER
        ):
            return MEDIA_UPLOAD_BODY_LIMIT_BYTES
        if _is_speech_route(route):
            return SPEECH_UPLOAD_BODY_LIMIT_BYTES
        return DEFAULT_BODY_LIMIT_BYTES


def is_speech_upload_scope(application: FastAPI, scope: Scope) -> bool:
    matches = _full_api_route_matches(application, scope)
    return len(matches) == 1 and _is_speech_route(matches[0][0])


def speech_upload_session_id(application: FastAPI, scope: Scope) -> str | None:
    matches = _full_api_route_matches(application, scope)
    if len(matches) != 1 or not _is_speech_route(matches[0][0]):
        return None
    path_params = matches[0][1].get("path_params", {})
    session_id = path_params.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def _full_api_route_matches(
    application: FastAPI,
    scope: Scope,
) -> list[tuple[APIRoute, Scope]]:
    matches: list[tuple[APIRoute, Scope]] = []
    for route in application.routes:
        if isinstance(route, APIRoute):
            match, child_scope = route.matches(scope)
            if match is Match.FULL:
                matches.append((route, child_scope))
            continue
        if type(route).__name__ != "_IncludedRouter" or route.matches(scope)[0] is not Match.FULL:
            continue
        original = getattr(route, "original_router", None)
        context = getattr(route, "include_context", None)
        nested_scope = dict(scope)
        prefix = str(getattr(context, "prefix", ""))
        if prefix and str(scope.get("path", "")).startswith(prefix):
            nested_scope["path"] = str(scope.get("path", ""))[len(prefix) :] or "/"
            nested_scope["root_path"] = str(scope.get("root_path", "")) + prefix
        for nested in getattr(original, "routes", ()):
            nested_match, child_scope = nested.matches(nested_scope)
            if isinstance(nested, APIRoute) and nested_match is Match.FULL:
                matches.append((nested, child_scope))
    return matches


def _is_speech_route(route: APIRoute) -> bool:
    return (
        route.name == SPEECH_UPLOAD_ROUTE_NAME
        and isinstance(route, SpeechUploadRoute)
        and getattr(route, "_focusproof_speech_route_marker", None)
        is _SPEECH_ROUTE_MARKER
    )
