from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal, TypedDict
from uuid import UUID

DASHSCOPE_ASR_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_ASR_MODEL = "qwen3-asr-flash"
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_DURATION_MS = 120_000
SPEECH_ACCEPTED_FORMATS = (
    "audio/webm;codecs=opus",
    "audio/wav",
    "audio/mpeg",
)


class AudioFormat(StrEnum):
    WEBM_OPUS = "webm_opus"
    WAV_PCM = "wav_pcm"
    MP3 = "mp3"


class LanguageHint(StrEnum):
    AUTO = "auto"
    ZH = "zh"
    EN = "en"


class TranscriptionState(StrEnum):
    ADMITTED = "admitted"
    UPLOADING = "uploading"
    SCANNING = "scanning"
    INSPECTING = "inspecting"
    DISPATCHING = "dispatching"
    SUCCEEDED = "succeeded"
    FAILED_TERMINAL = "failed_terminal"
    CANCELLED = "cancelled"
    AMBIGUOUS = "ambiguous"


SpeechDisabledReason = Literal[
    "asr_not_configured",
    "asr_configuration_invalid",
    "asr_prerequisites_unavailable",
]


class SpeechCapabilityEnabled(TypedDict):
    capabilityId: Literal["speech_transcription"]
    schemaVersion: Literal[1]
    enabled: Literal[True]
    formats: list[str]
    maxAudioBytes: int
    maxDurationSeconds: int
    languageHintsAccepted: list[str]
    languageHintEffect: Literal["metadata_only"]


class SpeechCapabilityDisabled(TypedDict):
    capabilityId: Literal["speech_transcription"]
    schemaVersion: Literal[1]
    enabled: Literal[False]
    reasonCode: SpeechDisabledReason


SpeechCapability = SpeechCapabilityEnabled | SpeechCapabilityDisabled


def _require_non_blank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


@dataclass(frozen=True, slots=True)
class SpeechSettings:
    provider: Literal["dashscope"]
    model: Literal["qwen3-asr-flash"]
    base_url: str
    api_key: str = field(repr=False)
    idempotency_hmac_active_version: str
    idempotency_hmac_keyring: tuple[tuple[str, str], ...] = field(repr=False)
    e2e_timeout_seconds: int = 120
    max_concurrency: int = 4

    def __post_init__(self) -> None:
        if self.provider != "dashscope":
            raise ValueError("provider must be dashscope")
        if self.model != DASHSCOPE_ASR_MODEL:
            raise ValueError("model must be qwen3-asr-flash")
        if self.base_url != DASHSCOPE_ASR_BASE_URL:
            raise ValueError("base_url must use the DashScope Beijing compatible endpoint")
        _require_non_blank(self.api_key, "api_key")
        _require_non_blank(
            self.idempotency_hmac_active_version,
            "idempotency_hmac_active_version",
        )
        keyring = dict(self.idempotency_hmac_keyring)
        if (
            len(keyring) != len(self.idempotency_hmac_keyring)
            or self.idempotency_hmac_active_version not in keyring
            or any(not version.strip() or not key.strip() for version, key in keyring.items())
        ):
            raise ValueError("idempotency HMAC keyring is invalid")
        if self.api_key in keyring.values():
            raise ValueError("idempotency HMAC key must be distinct from provider credentials")
        if self.e2e_timeout_seconds != 120:
            raise ValueError("e2e_timeout_seconds must be 120 for V1")
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")

    @property
    def business_timeout_seconds(self) -> int:
        return self.e2e_timeout_seconds - 5


@dataclass(frozen=True, slots=True)
class AudioFacts:
    audio_format: AudioFormat
    media_type: str
    codec: str
    byte_size: int
    duration_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.audio_format, AudioFormat):
            raise ValueError("audio_format must be a frozen AudioFormat")
        _require_non_blank(self.media_type, "media_type")
        _require_non_blank(self.codec, "codec")
        if self.media_type not in SPEECH_ACCEPTED_FORMATS:
            raise ValueError("media_type is not supported")
        if not 0 < self.byte_size <= MAX_AUDIO_BYTES:
            raise ValueError("byte_size is outside the supported range")
        if not 0 < self.duration_ms <= MAX_AUDIO_DURATION_MS:
            raise ValueError("duration_ms is outside the supported range")


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    request_id: UUID
    audio_path: Path
    facts: AudioFacts
    language_hint: LanguageHint = LanguageHint.AUTO

    def __post_init__(self) -> None:
        if not self.audio_path.is_absolute():
            raise ValueError("audio_path must be absolute")
        if not isinstance(self.facts, AudioFacts):
            raise ValueError("facts must be AudioFacts")
        if not isinstance(self.language_hint, LanguageHint):
            raise ValueError("language_hint must be a frozen LanguageHint")


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    request_id: UUID
    transcript: str = field(repr=False)
    provider: Literal["dashscope"]
    model: Literal["qwen3-asr-flash"]

    def __post_init__(self) -> None:
        if not self.transcript.strip():
            raise ValueError("transcript must not be blank")
        if self.provider != "dashscope":
            raise ValueError("provider must be dashscope")
        if self.model != DASHSCOPE_ASR_MODEL:
            raise ValueError("model must be qwen3-asr-flash")
