from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from focusproof.speech_core.errors import SpeechErrorCode


class SpeechTranscriptionResponse(BaseModel):
    requestId: str
    transcript: str
    provider: Literal["dashscope"]
    model: Literal["qwen3-asr-flash"]


_SPEECH_ERROR_HTTP: dict[SpeechErrorCode, tuple[int, bool]] = {
    SpeechErrorCode.AUDIO_TOO_LARGE: (413, False),
    SpeechErrorCode.AUDIO_TOO_LONG: (422, False),
    SpeechErrorCode.UNSUPPORTED_AUDIO_FORMAT: (415, False),
    SpeechErrorCode.INVALID_AUDIO: (422, False),
    SpeechErrorCode.TRANSCRIPTION_NO_SPEECH: (422, False),
    SpeechErrorCode.TRANSCRIPTION_TIMEOUT: (504, True),
    SpeechErrorCode.TRANSCRIPTION_RATE_LIMITED: (429, True),
    SpeechErrorCode.TRANSCRIPTION_PROVIDER_UNAVAILABLE: (503, True),
    SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS: (409, False),
    SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE: (410, False),
    SpeechErrorCode.TRANSCRIPTION_FAILED: (500, True),
    SpeechErrorCode.IDEMPOTENCY_CONFLICT: (409, False),
    SpeechErrorCode.TRANSCRIPTION_IN_PROGRESS: (409, True),
}


def speech_error_http(code: SpeechErrorCode) -> tuple[int, bool]:
    """Return the public HTTP status and retryability for one speech code."""

    return _SPEECH_ERROR_HTTP[code]
