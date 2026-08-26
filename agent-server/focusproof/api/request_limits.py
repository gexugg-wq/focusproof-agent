from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope


DEFAULT_BODY_LIMIT_BYTES = 256 * 1024
MEDIA_UPLOAD_BODY_LIMIT_BYTES = 11 * 1024 * 1024
MEDIA_UPLOAD_ROUTE_NAME = "upload_image_evidence"
_MEDIA_ROUTE_MARKER = object()


class MediaUploadRoute(APIRoute):
    _focusproof_media_route_marker = _MEDIA_ROUTE_MARKER


class BodyLimitResolver:
    def __init__(self, application: FastAPI) -> None:
        self._application = application

    def resolve(self, scope: Scope) -> int:
        full_matches: list[APIRoute] = []
        for route in self._application.routes:
            if not isinstance(route, APIRoute):
                continue
            match, _ = route.matches(scope)
            if match is Match.FULL:
                full_matches.append(route)
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
        return DEFAULT_BODY_LIMIT_BYTES
