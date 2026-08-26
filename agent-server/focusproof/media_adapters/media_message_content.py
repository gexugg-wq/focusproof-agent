from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from focusproof.media_core.ports import MediaObjectStore, MediaValidator, ReadOnlyMediaSource
from focusproof.media_core.limits import MAX_CANONICAL_MESSAGE_BYTES
from focusproof.persistence.repositories import MediaMessageArtifactFacts
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike


ALLOWED_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


class MediaMessageContentError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MediaMessageContent:
    facts: MediaMessageArtifactFacts
    payload: bytes
    width: int
    height: int


class MediaMessageContentProvider:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactoryLike,
        object_store: MediaObjectStore,
        *,
        image_validator: MediaValidator,
        max_image_bytes: int = MAX_CANONICAL_MESSAGE_BYTES,
    ) -> None:
        self._uow_factory = uow_factory
        self._object_store = object_store
        self._image_validator = image_validator
        self._max_image_bytes = max_image_bytes

    def get_facts(
        self,
        verified_user_id: str,
        session_id: str,
        evidence_id: str,
    ) -> MediaMessageArtifactFacts:
        with self._uow_factory() as uow:
            return uow.evidence.get_media_message_artifact(
                verified_user_id,
                session_id,
                evidence_id,
            )

    def get(
        self,
        verified_user_id: str,
        session_id: str,
        evidence_id: str,
    ) -> MediaMessageContent:
        facts = self.get_facts(verified_user_id, session_id, evidence_id)
        if facts.media_type not in ALLOWED_IMAGE_MEDIA_TYPES:
            raise MediaMessageContentError("media message MIME is unsupported")
        if facts.byte_size <= 0 or facts.byte_size > self._max_image_bytes:
            raise MediaMessageContentError("media message size is invalid")
        try:
            with self._object_store.open(facts.opaque_object_key) as stream:
                payload = stream.read(self._max_image_bytes + 1)
                trailing = stream.read(1)
        except (OSError, ValueError) as exc:
            raise MediaMessageContentError("media message object is unavailable") from exc
        if trailing or len(payload) != facts.byte_size or len(payload) > self._max_image_bytes:
            raise MediaMessageContentError("media message size differs")
        digest = sha256(payload).hexdigest()
        if digest != facts.normalized_sha256:
            raise MediaMessageContentError("media message digest differs")
        try:
            metadata = self._image_validator.validate(
                ReadOnlyMediaSource(
                    stream=BytesIO(payload),
                    byte_size=len(payload),
                    streaming_sha256=digest,
                ),
                facts.media_type,
            )
            width = _positive_dimension(metadata.attributes.get("width"))
            height = _positive_dimension(metadata.attributes.get("height"))
        except ValueError as exc:
            raise MediaMessageContentError("media message dimensions are invalid") from exc
        if (
            metadata.media_type != facts.media_type
            or metadata.byte_size != facts.byte_size
            or metadata.source_sha256 != facts.normalized_sha256
        ):
            raise MediaMessageContentError("media message validated facts differ")
        return MediaMessageContent(
            facts=facts,
            payload=payload,
            width=width,
            height=height,
        )


def _positive_dimension(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a dimension")
    if isinstance(value, (int, float)) and int(value) == value and int(value) > 0:
        return int(value)
    raise ValueError("dimension must be a positive integer")
