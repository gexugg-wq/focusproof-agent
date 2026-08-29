from __future__ import annotations

from enum import StrEnum


class SpeechErrorCode(StrEnum):
    AUDIO_TOO_LARGE = "audio_too_large"
    AUDIO_TOO_LONG = "audio_too_long"
    UNSUPPORTED_AUDIO_FORMAT = "unsupported_audio_format"
    INVALID_AUDIO = "invalid_audio"
    TRANSCRIPTION_NO_SPEECH = "transcription_no_speech"
    TRANSCRIPTION_TIMEOUT = "transcription_timeout"
    TRANSCRIPTION_RATE_LIMITED = "transcription_rate_limited"
    TRANSCRIPTION_PROVIDER_UNAVAILABLE = "transcription_provider_unavailable"
    TRANSCRIPTION_AMBIGUOUS = "transcription_ambiguous"
    TRANSCRIPTION_RESULT_UNAVAILABLE = "transcription_result_unavailable"
    TRANSCRIPTION_FAILED = "transcription_failed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    TRANSCRIPTION_IN_PROGRESS = "transcription_in_progress"


_SAFE_MESSAGES: dict[SpeechErrorCode, str] = {
    SpeechErrorCode.AUDIO_TOO_LARGE: "The audio file is too large.",
    SpeechErrorCode.AUDIO_TOO_LONG: "The audio recording is too long.",
    SpeechErrorCode.UNSUPPORTED_AUDIO_FORMAT: "The audio format is not supported.",
    SpeechErrorCode.INVALID_AUDIO: "The audio file is invalid.",
    SpeechErrorCode.TRANSCRIPTION_NO_SPEECH: "No speech was detected.",
    SpeechErrorCode.TRANSCRIPTION_TIMEOUT: "Transcription timed out.",
    SpeechErrorCode.TRANSCRIPTION_RATE_LIMITED: "Transcription is temporarily rate limited.",
    SpeechErrorCode.TRANSCRIPTION_PROVIDER_UNAVAILABLE: (
        "Transcription is temporarily unavailable."
    ),
    SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS: (
        "The transcription outcome is unknown; start a new attempt."
    ),
    SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE: (
        "The earlier transcription result is no longer available."
    ),
    SpeechErrorCode.TRANSCRIPTION_FAILED: "Transcription failed.",
    SpeechErrorCode.IDEMPOTENCY_CONFLICT: "The request key conflicts with another request.",
    SpeechErrorCode.TRANSCRIPTION_IN_PROGRESS: "Transcription is already in progress.",
}


class SpeechError(Exception):
    def __init__(self, code: SpeechErrorCode) -> None:
        self.code = code
        super().__init__(_SAFE_MESSAGES[code])


class SpeechAdmissionError(SpeechError):
    pass


class AudioValidationError(SpeechError):
    pass


class SpeechProviderError(SpeechError):
    pass


class SpeechAmbiguousError(SpeechProviderError):
    def __init__(self) -> None:
        super().__init__(SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS)
