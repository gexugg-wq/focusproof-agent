from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from focusproof.speech_core.errors import (
    SpeechAmbiguousError,
    SpeechErrorCode,
    SpeechProviderError,
)
from focusproof.speech_core.models import (
    AudioFacts,
    AudioFormat,
    LanguageHint,
    TranscriptionRequest,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ChunkedResponse(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class CaptureTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        status_code: int = 200,
        chunks: tuple[bytes, ...] = (b'{"text":"hello"}',),
        failure: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.chunks = chunks
        self.failure = failure
        self.calls = 0
        self.request: httpx.Request | None = None
        self.request_body = b""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.request = request
        self.request_body = b"".join([chunk async for chunk in request.stream])
        if self.failure is not None:
            raise self.failure
        return httpx.Response(
            self.status_code,
            request=request,
            stream=ChunkedResponse(*self.chunks),
        )


def _provider_type() -> type[Any]:
    from focusproof.speech_adapters.dashscope_asr import (
        DashScopeSpeechTranscriptionProvider,
    )

    return DashScopeSpeechTranscriptionProvider


def _request(tmp_path: Path, *, hint: LanguageHint = LanguageHint.ZH) -> TranscriptionRequest:
    audio_path = tmp_path / "private-recording.wav"
    audio_path.write_bytes(b"RIFF-private-audio")
    return TranscriptionRequest(
        request_id=uuid4(),
        audio_path=audio_path,
        facts=AudioFacts(
            audio_format=AudioFormat.WAV_PCM,
            media_type="audio/wav",
            codec="pcm",
            byte_size=18,
            duration_ms=750,
        ),
        language_hint=hint,
    )


async def _call(
    tmp_path: Path,
    transport: CaptureTransport,
    *,
    hint: LanguageHint = LanguageHint.ZH,
    api_key: str = "provider-secret",
    deadline: float | None = None,
):
    async with httpx.AsyncClient(transport=transport) as client:
        provider = _provider_type()(api_key=api_key, client=client)
        return await provider.transcribe(
            _request(tmp_path, hint=hint),
            deadline=deadline if deadline is not None else time.monotonic() + 5,
        )


async def test_posts_exact_beijing_contract_and_returns_only_text(tmp_path: Path) -> None:
    transport = CaptureTransport(
        chunks=(
            b'{"text":"hello world","emotion":"happy",',
            b'"acoustic":{"speaker":"x"}}',
        )
    )

    result = await _call(tmp_path, transport)

    assert result.transcript == "hello world"
    assert result.provider == "dashscope"
    assert result.model == "qwen3-asr-flash"
    assert transport.calls == 1
    request = transport.request
    assert request is not None
    assert str(request.url) == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/audio/transcriptions"
    )
    assert request.headers["authorization"] == "Bearer provider-secret"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="model"' in transport.request_body
    assert b"qwen3-asr-flash" in transport.request_body
    assert b'name="file"; filename="audio.wav"' in transport.request_body
    assert b"RIFF-private-audio" in transport.request_body
    assert b'name="language"' not in transport.request_body
    assert b'name="prompt"' not in transport.request_body
    assert not hasattr(result, "emotion")
    assert not hasattr(result, "acoustic")


@pytest.mark.parametrize("payload", [b'{"text":""}', b'{"text":"  \\n"}'])
async def test_blank_transcript_maps_to_no_speech(tmp_path: Path, payload: bytes) -> None:
    with pytest.raises(SpeechProviderError) as caught:
        await _call(tmp_path, CaptureTransport(chunks=(payload,)))

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_NO_SPEECH


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"{",
        b'{"other":"field"}',
        b'{"text":42}',
        b'{"text":"\\xff"}',
        b"\xff",
    ],
)
async def test_malformed_provider_response_is_bounded_failure(
    tmp_path: Path, payload: bytes
) -> None:
    with pytest.raises(SpeechProviderError) as caught:
        await _call(tmp_path, CaptureTransport(chunks=(payload,)))

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_FAILED


async def test_response_over_256_kib_is_rejected_without_reading_more(
    tmp_path: Path,
) -> None:
    chunks = (b"x" * (256 * 1024), b"x", b"must-not-be-needed")
    with pytest.raises(SpeechProviderError) as caught:
        await _call(tmp_path, CaptureTransport(chunks=chunks))

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_FAILED


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, SpeechErrorCode.TRANSCRIPTION_FAILED),
        (401, SpeechErrorCode.TRANSCRIPTION_FAILED),
        (429, SpeechErrorCode.TRANSCRIPTION_RATE_LIMITED),
        (500, SpeechErrorCode.TRANSCRIPTION_PROVIDER_UNAVAILABLE),
        (503, SpeechErrorCode.TRANSCRIPTION_PROVIDER_UNAVAILABLE),
    ],
)
async def test_http_errors_map_without_exposing_body(
    tmp_path: Path,
    status_code: int,
    expected: SpeechErrorCode,
) -> None:
    transport = CaptureTransport(
        status_code=status_code,
        chunks=(b'{"error":"provider-secret private transcript /private/path"}',),
    )

    with pytest.raises(SpeechProviderError) as caught:
        await _call(tmp_path, transport)

    assert caught.value.code is expected
    rendered = f"{caught.value!s} {caught.value!r}"
    assert "provider-secret" not in rendered
    assert "private transcript" not in rendered
    assert "/private/path" not in rendered
    assert transport.calls == 1


async def test_connect_failure_is_terminal_and_not_retried(tmp_path: Path) -> None:
    transport = CaptureTransport(failure=httpx.ConnectError("provider-secret /private"))

    with pytest.raises(SpeechProviderError) as caught:
        await _call(tmp_path, transport)

    assert not isinstance(caught.value, SpeechAmbiguousError)
    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_PROVIDER_UNAVAILABLE
    assert transport.calls == 1
    assert "provider-secret" not in str(caught.value)
    assert "/private" not in str(caught.value)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.RemoteProtocolError("private protocol data"),
        httpx.ReadTimeout("private transcript"),
        httpx.ReadError("provider-secret"),
        httpx.WriteError("private audio"),
    ],
)
async def test_indeterminate_transport_failure_is_ambiguous_and_not_retried(
    tmp_path: Path, failure: Exception
) -> None:
    transport = CaptureTransport(failure=failure)

    with pytest.raises(SpeechAmbiguousError) as caught:
        await _call(tmp_path, transport)

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS
    assert transport.calls == 1
    assert "private" not in str(caught.value)
    assert "provider-secret" not in str(caught.value)


async def test_expired_deadline_never_calls_provider(tmp_path: Path) -> None:
    transport = CaptureTransport()

    with pytest.raises(SpeechProviderError) as caught:
        await _call(tmp_path, transport, deadline=time.monotonic() - 1)

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_TIMEOUT
    assert transport.calls == 0


async def test_valid_utf16_json_is_rejected_as_non_utf8(tmp_path: Path) -> None:
    payload = '{"text":"private candidate"}'.encode("utf-16")

    with pytest.raises(SpeechProviderError) as caught:
        await _call(tmp_path, CaptureTransport(chunks=(payload,)))

    assert caught.value.code is SpeechErrorCode.TRANSCRIPTION_FAILED
    assert "private candidate" not in str(caught.value)
