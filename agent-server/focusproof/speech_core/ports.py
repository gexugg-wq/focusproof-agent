from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from focusproof.speech_core.models import AudioFacts, TranscriptionRequest, TranscriptionResult


@runtime_checkable
class SpeechTranscriptionProvider(Protocol):
    async def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        deadline: float,
    ) -> TranscriptionResult: ...


@runtime_checkable
class AudioInspector(Protocol):
    async def inspect(
        self,
        path: Path,
        *,
        declared_media_type: str | None,
        deadline: float,
    ) -> AudioFacts: ...
