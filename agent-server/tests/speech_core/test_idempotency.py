from focusproof.speech_core.idempotency import request_fingerprint
from focusproof.speech_core.models import LanguageHint


def test_request_fingerprint_is_stable_for_exact_audio_and_language_hint() -> None:
    first = request_fingerprint(
        payload_sha256="a" * 64,
        language_hint=LanguageHint.ZH,
        media_type="audio/wav",
    )
    replay = request_fingerprint(
        payload_sha256="a" * 64,
        language_hint=LanguageHint.ZH,
        media_type="audio/wav",
    )

    assert first == replay
    assert len(first) == 64


def test_request_fingerprint_changes_when_audio_payload_changes() -> None:
    original = request_fingerprint(
        payload_sha256="a" * 64,
        language_hint=LanguageHint.AUTO,
        media_type="audio/wav",
    )
    changed = request_fingerprint(
        payload_sha256="b" * 64,
        language_hint=LanguageHint.AUTO,
        media_type="audio/wav",
    )

    assert changed != original


def test_request_fingerprint_changes_when_language_hint_changes() -> None:
    automatic = request_fingerprint(
        payload_sha256="a" * 64,
        language_hint=LanguageHint.AUTO,
        media_type="audio/wav",
    )
    chinese = request_fingerprint(
        payload_sha256="a" * 64,
        language_hint=LanguageHint.ZH,
        media_type="audio/wav",
    )

    assert chinese != automatic


def test_request_fingerprint_changes_when_declared_media_type_changes() -> None:
    wave = request_fingerprint(
        payload_sha256="a" * 64,
        language_hint=LanguageHint.AUTO,
        media_type="audio/wav",
    )
    mpeg = request_fingerprint(
        payload_sha256="a" * 64,
        language_hint=LanguageHint.AUTO,
        media_type="audio/mpeg",
    )

    assert mpeg != wave
