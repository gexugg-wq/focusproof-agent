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
    elif payload["evidenceType"] == "url" and isinstance(
        evidence.get("source"), Mapping
    ):
        safe_source = safe_url_metadata(evidence["source"])
        if safe_source is not None:
            payload["source"] = safe_source
    return payload


def safe_url_metadata(value: Mapping[str, Any]) -> dict[str, object] | None:
    scheme = value.get("scheme")
    hostname = value.get("hostname")
    port = value.get("port")
    digest = value.get("url_sha256")
    if (
        scheme not in {"http", "https"}
        or not isinstance(hostname, str)
        or not hostname
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest.lower())
        or isinstance(port, bool)
        or (port is not None and not isinstance(port, int))
        or (isinstance(port, int) and not 1 <= port <= 65535)
    ):
        return None
    host = f"[{hostname}]" if ":" in hostname else hostname
    authority = f"{host}:{port}" if port is not None else host
    result: dict[str, object] = {
        "scheme": scheme,
        "hostname": hostname,
        "origin": f"{scheme}://{authority}",
        "path_redacted": bool(value.get("path_redacted")),
        "url_sha256": digest.lower(),
    }
    if port is not None:
        result["port"] = port
    return result


def sanitize_verification_facts(
    capability: str,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    if capability != "url":
        return dict(facts)

    safe: dict[str, Any] = {}
    source_urls: list[str] = []
    current_url = facts.get("url")
    if isinstance(current_url, Mapping):
        metadata = safe_url_metadata(current_url)
        if metadata is not None:
            safe["url"] = metadata
    legacy_url = facts.get("normalized_url")
    if "url" not in safe and isinstance(legacy_url, str):
        safe["url"] = redact_url(legacy_url)
        source_urls.append(legacy_url)

    redirects: list[dict[str, object]] = []
    raw_redirects = facts.get("redirect_chain")
    if isinstance(raw_redirects, list):
        for redirect in raw_redirects:
            if isinstance(redirect, str):
                redirects.append(redact_url(redirect))
                source_urls.append(redirect)
            elif isinstance(redirect, Mapping):
                metadata = safe_url_metadata(redirect)
                if metadata is not None:
                    redirects.append(metadata)
    safe["redirect_chain"] = redirects

    for key, expected_type in (
        ("status_code", int),
        ("content_type", str),
        ("content_length", int),
    ):
        value = facts.get(key)
        if isinstance(value, expected_type):
            safe[key] = value
    for key in ("title", "text_excerpt"):
        value = facts.get(key)
        if value is None or isinstance(value, str):
            safe[key] = redact_url_text(value, source_urls)
    return safe


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
        for part in parsed.query.split("&"):
            raw_key, separator, raw_value = part.partition("=")
            tokens.update(_decoded_variants(raw_key))
            if separator:
                tokens.update(_decoded_variants(raw_value))
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            tokens.update(_decoded_variants(key))
            tokens.update(_decoded_variants(value))
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
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def _decoded_variants(value: str) -> set[str]:
    variants: set[str] = set()
    current = value
    for _ in range(3):
        if current:
            variants.add(current)
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
    return variants


def _safe_port(parsed: SplitResult) -> int | None:
    try:
        return parsed.port
    except ValueError:
        return None
