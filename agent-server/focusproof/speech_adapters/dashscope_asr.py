from __future__ import annotations

import asyncio
import base64
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
    TranscriptionRequest,
    TranscriptionResult,
)

_CHAT_COMPLETIONS_URL = f"{DASHSCOPE_ASR_BASE_URL}/chat/completions"
_MAX_RESPONSE_BYTES = 256 * 1024


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
            async with asyncio.timeout_at(deadline):
                audio_data = await asyncio.to_thread(request.audio_path.read_bytes)
                encoded_audio = await asyncio.to_thread(base64.b64encode, audio_data)
                request_payload = {
                    "model": DASHSCOPE_ASR_MODEL,
                    "messages": [{
                        "role": "user",
                        "content": [{
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:{request.facts.media_type};base64,{encoded_audio.decode('ascii')}"
                            },
                        }],
                    }],
                    "stream": False,
                }
                async with self._client.stream(
                    "POST",
                    _CHAT_COMPLETIONS_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=request_payload,
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
        choices = decoded.get("choices")
        if not isinstance(choices, list) or not choices:
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_FAILED)
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_FAILED)
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise SpeechProviderError(SpeechErrorCode.TRANSCRIPTION_FAILED)
        transcript = message.get("content")
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
