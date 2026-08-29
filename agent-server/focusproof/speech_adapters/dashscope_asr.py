from __future__ import annotations

import asyncio
import json
import time
from types import TracebackType
from typing import Any

import httpx

from focusproof.speech_core.errors import (
    SpeechAmbiguousError,
    SpeechErrorCode,
    SpeechProviderError,
)
from focusproof.speech_core.models import (
    DASHSCOPE_ASR_BASE_URL,
    DASHSCOPE_ASR_MODEL,
    AudioFormat,
    TranscriptionRequest,
    TranscriptionResult,
)

_TRANSCRIPTIONS_URL = f"{DASHSCOPE_ASR_BASE_URL}/audio/transcriptions"
_MAX_RESPONSE_BYTES = 256 * 1024
_SAFE_FILENAMES = {
    AudioFormat.WEBM_OPUS: "audio.webm",
    AudioFormat.WAV_PCM: "audio.wav",
    AudioFormat.MP3: "audio.mp3",
}


class DashScopeSpeechTranscriptionProvider:
    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DashScope API key must not be blank")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        deadline: float,
    ) -> TranscriptionResult:
        if deadline <= time.monotonic():
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_TIMEOUT)
        try:
            with request.audio_path.open("rb") as audio:
                async with asyncio.timeout_at(deadline):
                    async with self._client.stream(
                        "POST",
                        _TRANSCRIPTIONS_URL,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        data={"model": DASHSCOPE_ASR_MODEL},
                        files={
                            "file": (
                                _SAFE_FILENAMES[request.facts.audio_format],
                                audio,
                                request.facts.media_type,
                            )
                        },
                    ) as response:
                        self._raise_for_status(response.status_code)
                        payload = await self._read_bounded(response)
        except SpeechProviderError:
            raise
        except httpx.ConnectError:
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_PROVIDER_UNAVAILABLE) from None
        except httpx.ConnectTimeout:
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_PROVIDER_UNAVAILABLE) from None
        except httpx.TransportError:
            raise SpeechAmbiguousError() from None
        except TimeoutError:
            raise SpeechAmbiguousError() from None
        except (OSError, KeyError):
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_FAILED) from None
        return self._parse_result(request, payload)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> DashScopeSpeechTranscriptionProvider:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.aclose()

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code == 429:
            code = SpeechErrorCode.TRANSCRIPTION_RATE_LIMITED
        elif status_code >= 500:
            code = SpeechErrorCode.TRANSCRIPTION_PROVIDER_UNAVAILABLE
        else:
            code = SpeechErrorCode.TRANSCRIPTION_FAILED
        raise SpeechProviderError(code)

    @staticmethod
    async def _read_bounded(response: httpx.Response) -> bytes:
        payload = bytearray()
        async for chunk in response.aiter_bytes():
            if len(payload) + len(chunk) > _MAX_RESPONSE_BYTES:
                raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_FAILED)
            payload.extend(chunk)
        return bytes(payload)

    @staticmethod
    def _parse_result(
        request: TranscriptionRequest,
        payload: bytes,
    ) -> TranscriptionResult:
        try:
            response_text = payload.decode("utf-8", errors="strict")
            decoded: Any = json.loads(response_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_FAILED) from None
        if not isinstance(decoded, dict):
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_FAILED)
        transcript = decoded.get("text")
        if not isinstance(transcript, str):
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_FAILED)
        if not transcript.strip():
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_NO_SPEECH)
        return TranscriptionResult(
            request_id=request.request_id,
            transcript=transcript,
            provider="dashscope",
            model="qwen3-asr-flash",
        )
