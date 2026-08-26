from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from typing import Iterator

import pytest

from focusproof.media_adapters.media_message_content import (
    MediaMessageContentError,
    MediaMessageContentProvider,
)
from focusproof.media_core.ports import ReadOnlyMediaSource, ValidatedMediaMetadata
from focusproof.persistence.repositories import MediaMessageArtifactFacts


class Evidence:
    def __init__(self, facts: MediaMessageArtifactFacts) -> None:
        self.facts = facts
        self.calls: list[tuple[str, str, str]] = []

    def get_media_message_artifact(
        self, owner: str, session: str, evidence: str
    ) -> MediaMessageArtifactFacts:
        self.calls.append((owner, session, evidence))
        return self.facts


class Uow:
    def __init__(self, evidence: Evidence) -> None:
        self.evidence = evidence

    def __enter__(self) -> Uow:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class Validator:
    def __init__(self, *, width: object = 1, height: object = 1) -> None:
        self.width = width
        self.height = height

    def validate(
        self,
        source: ReadOnlyMediaSource,
        declared_media_type: str | None,
    ) -> ValidatedMediaMetadata:
        assert declared_media_type is not None
        return ValidatedMediaMetadata(
            media_type=declared_media_type,
            byte_size=source.byte_size,
            source_sha256=source.streaming_sha256,
            attributes={"width": self.width, "height": self.height},
        )


class Store:
    def __init__(self, payload: bytes | BaseException) -> None:
        self.payload = payload
        self.keys: list[str] = []

    @contextmanager
    def open(self, key: str) -> Iterator[BytesIO]:
        self.keys.append(key)
        if isinstance(self.payload, BaseException):
            raise self.payload
        yield BytesIO(self.payload)


def facts(
    payload: bytes, *, mime: str = "image/png", size: int | None = None, digest: str | None = None
) -> MediaMessageArtifactFacts:
    actual_digest = digest or sha256(payload).hexdigest()
    return MediaMessageArtifactFacts(
        evidence_id="ev-image",
        receipt_id="receipt-image",
        attempt_id="attempt-image",
        scan_result="clean",
        artifact_ref="focusproof-artifact://media-one",
        artifact_sha256=actual_digest,
        opaque_object_key="0123456789abcdef0123456789abcdef",
        media_type=mime,
        normalized_sha256=actual_digest,
        byte_size=len(payload) if size is None else size,
        width=1,
        height=1,
    )


def test_provider_reads_only_opaque_key_and_returns_bounded_payload() -> None:
    payload = b"normalized-image-bytes"
    evidence = Evidence(facts(payload))
    store = Store(payload)
    provider = MediaMessageContentProvider(lambda: Uow(evidence), store, image_validator=Validator())

    content = provider.get("owner-one", "session-one", "ev-image")

    assert evidence.calls == [("owner-one", "session-one", "ev-image")]
    assert store.keys == ["0123456789abcdef0123456789abcdef"]
    assert content.payload == payload
    assert content.facts == facts(payload)
    assert (content.width, content.height) == (1, 1)


def test_provider_accepts_the_canonical_ingestion_message_limit() -> None:
    payload = b"x" * (10 * 1024 * 1024)
    provider = MediaMessageContentProvider(
        lambda: Uow(Evidence(facts(payload))),
        Store(payload),
        image_validator=Validator(),
    )

    content = provider.get("owner", "session", "evidence")

    assert len(content.payload) == len(payload)


@pytest.mark.parametrize(
    ("artifact", "stored", "limit", "match"),
    [
        (facts(b"x", mime="text/plain"), b"x", 100, "MIME"),
        (facts(b"x", size=2), b"x", 100, "size"),
        (facts(b"x", digest="0" * 64), b"x", 100, "digest"),
        (facts(b"xx"), b"xx", 1, "size"),
        (facts(b"x"), FileNotFoundError(), 100, "unavailable"),
    ],
)
def test_provider_fails_closed_for_invalid_or_missing_content(
    artifact: MediaMessageArtifactFacts, stored: bytes | BaseException, limit: int, match: str
) -> None:
    provider = MediaMessageContentProvider(
        lambda: Uow(Evidence(artifact)),
        Store(stored),
        image_validator=Validator(),
        max_image_bytes=limit,
    )

    with pytest.raises(MediaMessageContentError, match=match):
        provider.get("owner", "session", "evidence")


def test_facts_lookup_does_not_open_object_payload() -> None:
    payload = b"normalized-image-bytes"
    evidence = Evidence(facts(payload))
    store = Store(AssertionError("facts lookup must not open object bytes"))
    provider = MediaMessageContentProvider(lambda: Uow(evidence), store, image_validator=Validator())

    result = provider.get_facts("owner-one", "session-one", "ev-image")

    assert result == facts(payload)
    assert evidence.calls == [("owner-one", "session-one", "ev-image")]
    assert store.keys == []



def test_provider_fails_closed_for_invalid_authoritative_dimensions() -> None:
    payload = b"normalized-image-bytes"
    provider = MediaMessageContentProvider(
        lambda: Uow(Evidence(facts(payload))),
        Store(payload),
        image_validator=Validator(width=0),
    )

    with pytest.raises(MediaMessageContentError, match="dimensions"):
        provider.get("owner", "session", "evidence")
