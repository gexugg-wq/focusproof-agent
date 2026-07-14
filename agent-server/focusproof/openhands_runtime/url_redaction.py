from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import SplitResult, parse_qsl, unquote, urlsplit, urlunsplit


def redact_url(url: str) -> dict[str, object]:
    """Return the only URL representation allowed outside authoritative storage."""
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = _safe_port(parsed)
    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    public_port = None if default_port else port
    host = f"[{hostname}]" if ":" in hostname else hostname
    public_authority = host
    if public_port is not None:
        public_authority = f"{public_authority}:{public_port}"

    canonical_authority = public_authority
    if parsed.username is not None:
        credentials = parsed.username
        if parsed.password is not None:
            credentials = f"{credentials}:{parsed.password}"
        canonical_authority = f"{credentials}@{canonical_authority}"
    canonical = urlunsplit(
        (
            scheme,
            canonical_authority,
            parsed.path or "/",
            parsed.query,
            parsed.fragment,
        )
    )
    result: dict[str, object] = {
        "scheme": scheme,
        "hostname": hostname,
        "origin": f"{scheme}://{public_authority}",
        "path_redacted": bool(parsed.path and parsed.path != "/"),
        "url_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
    if public_port is not None:
        result["port"] = public_port
    return result


def safe_evidence_payload(evidence: Mapping[str, Any]) -> dict[str, object]:
    """Remove evidence bodies and arbitrary metadata before runtime ingestion."""
    payload: dict[str, object] = {
        "evidenceId": evidence.get("evidenceId", evidence.get("evidence_id")),
        "evidenceType": evidence.get("evidenceType", evidence.get("evidence_type")),
        "contentHash": evidence.get("contentHash", evidence.get("content_hash")),
    }
    source_url = evidence.get("sourceUrl", evidence.get("source_url"))
    if payload["evidenceType"] == "url" and isinstance(source_url, str) and source_url:
        payload["source"] = redact_url(source_url)
    return payload


def redact_url_text(text: str | None, urls: Iterable[str]) -> str | None:
    if text is None:
        return None
    redacted = text
    tokens: set[str] = set()
    for url in urls:
        parsed = urlsplit(url)
        for value in (parsed.username, parsed.password, parsed.fragment):
            if value:
                tokens.update({value, unquote(value)})
        tokens.update(
            token
            for segment in parsed.path.split("/")
            for token in (segment, unquote(segment))
            if token
        )
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            tokens.update(token for token in (key, value, unquote(key), unquote(value)) if token)
    for token in sorted(tokens, key=len, reverse=True):
        redacted = redacted.replace(token, "[redacted]")
    return redacted


def sanitize_source_refs(source_refs: Iterable[str]) -> list[str]:
    return [
        f"url-sha256:{redact_url(ref)['url_sha256']}"
        if _looks_like_url(ref)
        else ref
        for ref in source_refs
    ]


def _looks_like_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def _safe_port(parsed: SplitResult) -> int | None:
    try:
        return parsed.port
    except ValueError:
        return None
