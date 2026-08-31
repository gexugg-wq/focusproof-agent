from __future__ import annotations

from hashlib import sha256
from string import hexdigits

from focusproof.speech_core.models import LanguageHint

_FINGERPRINT_DOMAIN = b"focusproof:speech-request-fingerprint:v1"


def request_fingerprint(
    *,
    payload_sha256: str,
    language_hint: LanguageHint,
    media_type: str,
) -> str:
    """Return the canonical request identity without retaining audio bytes."""
    if (
        len(payload_sha256) != 64
        or any(character not in hexdigits for character in payload_sha256)
    ):
        raise ValueError("payload_sha256 must be a SHA-256 hex digest")
    if not isinstance(language_hint, LanguageHint):
        raise ValueError("language_hint must be a LanguageHint")
    if not media_type.strip():
        raise ValueError("media_type must not be blank")

    digest = sha256()
    digest.update(_FINGERPRINT_DOMAIN)
    for component in (
        bytes.fromhex(payload_sha256),
        language_hint.value.encode("utf-8"),
        media_type.encode("utf-8"),
    ):
        digest.update(len(component).to_bytes(4, byteorder="big"))
        digest.update(component)
    return digest.hexdigest()
