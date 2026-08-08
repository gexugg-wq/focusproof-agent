from __future__ import annotations

import re
from contextlib import closing
from collections.abc import Callable
from dataclasses import dataclass
from html import unescape
from threading import Event
from time import monotonic
from urllib.parse import urljoin

import httpx

from focusproof.openhands_runtime.tools.url_safety import SafeUrl, UrlSafetyPolicy

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_SUPPORTED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xhtml+xml",
    "application/xml",
)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class FetchedUrl:
    final_url: str
    status_code: int
    content_type: str
    content_length: int
    redirect_chain: tuple[str, ...]
    title: str | None
    text_excerpt: str | None


class UrlFetchError(RuntimeError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class BoundedUrlFetcher:
    def __init__(
        self,
        *,
        policy: UrlSafetyPolicy,
        client: httpx.Client,
        total_timeout_seconds: float,
        clock: Callable[[], float] = monotonic,
        max_redirects: int = 3,
        max_bytes: int = 1_048_576,
    ) -> None:
        if client.follow_redirects:
            raise ValueError("URL verifier client must disable automatic redirects")
        if max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be positive")
        self._policy = policy
        self._client = client
        self._total_timeout_seconds = total_timeout_seconds
        self._clock = clock
        self._max_redirects = max_redirects
        self._max_bytes = max_bytes

    @property
    def total_timeout_seconds(self) -> float:
        return self._total_timeout_seconds

    def fetch(
        self,
        source_url: str,
        *,
        interrupt_event: Event | None = None,
    ) -> FetchedUrl:
        deadline = self._clock() + self._total_timeout_seconds
        self._remaining(deadline, interrupt_event)
        safe = self._policy.validate(source_url)
        self._remaining(deadline, interrupt_event)
        redirects: list[str] = []
        while True:
            request_timeout = self._remaining(deadline, interrupt_event)
            request_url, host_header = _pinned_request_target(safe)
            try:
                request = self._client.build_request(
                    "GET",
                    request_url,
                    headers={"Host": host_header, "Connection": "close"},
                    extensions={"sni_hostname": safe.hostname},
                )
                request.headers.pop("cookie", None)
                request.extensions["timeout"] = {
                    "connect": request_timeout,
                    "read": request_timeout,
                    "write": request_timeout,
                    "pool": request_timeout,
                }
                with closing(
                    self._client.send(
                        request,
                        stream=True,
                        follow_redirects=False,
                    )
                ) as response:
                    self._remaining(deadline, interrupt_event)
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise UrlFetchError(
                                "redirect_location_missing",
                                "The URL redirect did not include a target.",
                            )
                        if len(redirects) >= self._max_redirects:
                            raise UrlFetchError(
                                "too_many_redirects",
                                "The URL exceeded the redirect limit.",
                            )
                        target = urljoin(safe.normalized, location)
                        self._remaining(deadline, interrupt_event)
                        safe = self._policy.validate(target)
                        self._remaining(deadline, interrupt_event)
                        redirects.append(safe.normalized)
                        continue
                    return self._read_response(
                        response,
                        safe.normalized,
                        redirects,
                        deadline,
                        interrupt_event,
                    )
            except httpx.TimeoutException as exc:
                raise UrlFetchError(
                    "network_timeout", "The URL request timed out."
                ) from exc
            except httpx.RequestError as exc:
                raise UrlFetchError(
                    "network_unavailable", "The URL could not be retrieved."
                ) from exc

    def _read_response(
        self,
        response: httpx.Response,
        final_url: str,
        redirects: list[str],
        deadline: float,
        interrupt_event: Event | None,
    ) -> FetchedUrl:
        self._remaining(deadline, interrupt_event)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if not any(
            content_type.startswith(allowed) for allowed in _SUPPORTED_CONTENT_TYPES
        ):
            raise UrlFetchError(
                "content_type_unsupported",
                "The URL content type is not supported.",
            )
        declared_length = response.headers.get("content-length")
        if declared_length is not None:
            try:
                parsed_length = int(declared_length)
            except ValueError as exc:
                raise UrlFetchError(
                    "content_length_invalid",
                    "The URL returned an invalid content length.",
                ) from exc
            if parsed_length > self._max_bytes:
                raise UrlFetchError(
                    "response_too_large",
                    "The URL response exceeded the size limit.",
                )

        body = bytearray()
        for chunk in response.iter_bytes():
            self._remaining(deadline, interrupt_event)
            if len(body) + len(chunk) > self._max_bytes:
                raise UrlFetchError(
                    "response_too_large",
                    "The URL response exceeded the size limit.",
                )
            body.extend(chunk)
        self._remaining(deadline, interrupt_event)
        text = bytes(body).decode("utf-8", errors="replace")
        self._remaining(deadline, interrupt_event)
        title = _extract_title(text) if content_type in {"text/html", "application/xhtml+xml"} else None
        self._remaining(deadline, interrupt_event)
        excerpt = _extract_text(text, is_html=content_type in {"text/html", "application/xhtml+xml"})
        self._remaining(deadline, interrupt_event)
        return FetchedUrl(
            final_url=final_url,
            status_code=response.status_code,
            content_type=content_type,
            content_length=len(body),
            redirect_chain=tuple(redirects),
            title=title,
            text_excerpt=excerpt,
        )

    def _remaining(self, deadline: float, interrupt_event: Event | None) -> float:
        if interrupt_event is not None and interrupt_event.is_set():
            raise UrlFetchError(
                "network_timeout",
                "The URL request was interrupted.",
            )
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise UrlFetchError(
                "network_timeout",
                "The URL request timed out.",
            )
        return remaining


def _extract_title(text: str) -> str | None:
    match = _TITLE_RE.search(text)
    if match is None:
        return None
    title = _WHITESPACE_RE.sub(" ", unescape(_TAG_RE.sub(" ", match.group(1)))).strip()
    return title[:512] or None


def _pinned_request_target(safe: SafeUrl) -> tuple[httpx.URL, str]:
    original = httpx.URL(safe.normalized)
    address = safe.addresses[0]
    request_url = original.copy_with(host=address)
    host = f"[{safe.hostname}]" if ":" in safe.hostname else safe.hostname
    default_port = 443 if original.scheme == "https" else 80
    if original.port is not None and original.port != default_port:
        host = f"{host}:{original.port}"
    return request_url, host


def _extract_text(text: str, *, is_html: bool) -> str | None:
    value = _TAG_RE.sub(" ", text) if is_html else text
    value = _WHITESPACE_RE.sub(" ", unescape(value)).strip()
    return value[:2_000] or None
