from __future__ import annotations

import logging
import os
import stat
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from focusproof.api.speech_admission import SpeechTaskRegistry
from focusproof.api.speech_models import SpeechTranscriptionResponse
from focusproof.api.speech_routes import SuffixAwareAudioInspector, build_speech_router
from focusproof.persistence.repositories import SpeechAdmissionToken
from focusproof.speech_application import SpeechExecutionAdmission, UploadedSpeechFile
from focusproof.speech_core.errors import SpeechErrorCode, SpeechProviderError
from focusproof.speech_core.models import LanguageHint, TranscriptionResult


class _Service:
    def __init__(
        self,
        tmp_path: Path,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.failure = failure
        self.calls = 0
        self.languages: list[LanguageHint] = []
        self.deadlines: list[float] = []
        self.uploaded: list[UploadedSpeechFile] = []
        self.temp_modes: list[tuple[int, int]] = []

    async def execute(
        self,
        admission: SpeechExecutionAdmission,
        upload: Any,
        language_hint: LanguageHint,
        disconnect_probe: Any,
    ) -> TranscriptionResult:
        self.calls += 1
        self.languages.append(language_hint)
        self.deadlines.append(admission.deadline)
        assert not await disconnect_probe()
        destination = self.tmp_path / f"{admission.token.request_id}.wav"
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.uploaded.append(await upload.write_to(destination, deadline=admission.deadline - 5))
        self.temp_modes.append(
            (
                stat.S_IMODE(destination.parent.stat().st_mode),
                stat.S_IMODE(destination.stat().st_mode),
            )
        )
        destination.unlink(missing_ok=True)
        if self.failure is not None:
            raise self.failure
        return TranscriptionResult(
            request_id=UUID(admission.token.request_id),
            transcript="private live transcript",
            provider="dashscope",
            model="qwen3-asr-flash",
        )


def _admission(deadline: float | None = None) -> SpeechExecutionAdmission:
    token = SpeechAdmissionToken(
        request_id=str(uuid4()),
        owner_user_id="user-1",
        session_id="sess-1",
        lease_owner="worker-1",
        lease_generation=1,
        lease_expires_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    return SpeechExecutionAdmission(
        token=token,
        deadline=deadline if deadline is not None else time.monotonic() + 120,
    )


def _client(
    service: _Service,
    *,
    enabled: bool = True,
) -> TestClient:
    application = FastAPI()
    application.state.speech_service = service
    application.state.speech_task_registry = SpeechTaskRegistry()
    application.state.speech_capability = {"enabled": enabled}
    application.include_router(build_speech_router())

    @application.middleware("http")
    async def inject_admission(request: Request, call_next: Any) -> Any:
        request.state.speech_admission = _admission()
        return await call_next(request)

    return TestClient(application, raise_server_exceptions=False)


def _post(
    client: TestClient,
    *,
    language: str = "auto",
    files: Any = None,
) -> Any:
    return client.post(
        "/sessions/sess-1/transcriptions",
        headers={"Idempotency-Key": str(uuid4())},
        files=files or {"file": ("voice.wav", b"RIFF-private-audio", "audio/wav")},
        data={"languageHint": language},
    )


def test_live_success_is_typed_and_contains_only_four_safe_fields(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _Service(tmp_path)
    caplog.set_level(logging.INFO)

    with _client(service) as client:
        response = _post(client, language="zh")

    assert response.status_code == 200
    assert response.json() == {
        "requestId": response.json()["requestId"],
        "transcript": "private live transcript",
        "provider": "dashscope",
        "model": "qwen3-asr-flash",
    }
    UUID(response.json()["requestId"])
    assert set(response.json()) == set(SpeechTranscriptionResponse.model_fields)
    assert service.calls == 1
    assert service.languages == [LanguageHint.ZH]
    assert len(service.deadlines) == 1
    assert service.deadlines[0] > time.monotonic() + 100
    assert "private live transcript" not in caplog.text
    assert "RIFF-private-audio" not in caplog.text


@pytest.mark.parametrize("language", ["fr", "ZH", "", "auto "])
def test_language_hint_is_closed_enum_before_service(
    tmp_path: Path,
    language: str,
) -> None:
    service = _Service(tmp_path)
    with _client(service) as client:
        response = _post(client, language=language)

    assert response.status_code == 422
    assert response.json() == {"code": "invalid_language_hint", "retryable": False}
    assert service.calls == 0


def test_exactly_one_audio_file_is_required(tmp_path: Path) -> None:
    service = _Service(tmp_path)
    files = [
        ("file", ("one.wav", b"one", "audio/wav")),
        ("file", ("two.wav", b"two", "audio/wav")),
    ]
    with _client(service) as client:
        response = _post(client, files=files)

    assert response.status_code == 422
    assert response.json() == {"code": "one_audio_file_required", "retryable": False}
    assert service.calls == 0


def test_rejects_upload_file_under_any_non_file_field(tmp_path: Path) -> None:
    service = _Service(tmp_path)
    files = [
        ("file", ("one.wav", b"one", "audio/wav")),
        ("other", ("hidden.wav", b"private-hidden", "audio/wav")),
    ]
    with _client(service) as client:
        response = _post(client, files=files)

    assert response.status_code == 422
    assert response.json() == {
        "code": "one_audio_file_required",
        "retryable": False,
    }
    assert service.calls == 0


def test_capability_disabled_fails_closed_before_service(tmp_path: Path) -> None:
    service = _Service(tmp_path)
    with _client(service, enabled=False) as client:
        response = _post(client)

    assert response.status_code == 503
    assert response.json() == {"code": "speech_disabled", "retryable": False}
    assert service.calls == 0


@pytest.mark.parametrize(
    ("code", "status", "retryable"),
    [
        (SpeechErrorCode.AUDIO_TOO_LARGE, 413, False),
        (SpeechErrorCode.AUDIO_TOO_LONG, 422, False),
        (SpeechErrorCode.UNSUPPORTED_AUDIO_FORMAT, 415, False),
        (SpeechErrorCode.INVALID_AUDIO, 422, False),
        (SpeechErrorCode.TRANSCRIPTION_NO_SPEECH, 422, False),
        (SpeechErrorCode.TRANSCRIPTION_TIMEOUT, 504, True),
        (SpeechErrorCode.TRANSCRIPTION_RATE_LIMITED, 429, True),
        (SpeechErrorCode.TRANSCRIPTION_PROVIDER_UNAVAILABLE, 503, True),
        (SpeechErrorCode.TRANSCRIPTION_AMBIGUOUS, 409, False),
        (SpeechErrorCode.TRANSCRIPTION_RESULT_UNAVAILABLE, 410, False),
        (SpeechErrorCode.TRANSCRIPTION_FAILED, 500, True),
        (SpeechErrorCode.IDEMPOTENCY_CONFLICT, 409, False),
        (SpeechErrorCode.TRANSCRIPTION_IN_PROGRESS, 409, False),
    ],
)
def test_stable_speech_errors_have_typed_status_and_no_private_detail(
    tmp_path: Path,
    code: SpeechErrorCode,
    status: int,
    retryable: bool,
) -> None:
    service = _Service(tmp_path, failure=SpeechProviderError(code))
    with _client(service) as client:
        response = _post(client)

    assert response.status_code == status
    assert response.json() == {"code": code.value, "retryable": retryable}
    assert "private" not in response.text


def test_single_file_stream_enforces_10_mib_without_second_service_call(
    tmp_path: Path,
) -> None:
    service = _Service(tmp_path)
    with _client(service) as client:
        response = _post(
            client,
            files={"file": ("voice.wav", b"a" * (10 * 1024 * 1024 + 1), "audio/wav")},
        )

    assert response.status_code == 413
    assert response.json()["code"] == "audio_too_large"
    assert service.calls == 1


def test_streaming_upload_forces_private_directory_and_file_modes(
    tmp_path: Path,
) -> None:
    service = _Service(tmp_path)
    previous_umask = os.umask(0)
    try:
        with _client(service) as client:
            response = _post(client)
    finally:
        os.umask(previous_umask)

    assert response.status_code == 200
    assert service.temp_modes == [(0o700, 0o600)]


def test_create_app_registers_speech_route_and_admission_before_multipart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from focusproof.api.app import create_app
    from focusproof.api.speech_admission import SpeechAdmissionMiddleware

    monkeypatch.setenv("FOCUSPROOF_MEDIA_ENABLED", "false")
    application = create_app()

    assert (
        str(application.url_path_for("create_speech_transcription", session_id="sess-1"))
        == "/sessions/sess-1/transcriptions"
    )
    middleware_classes = [item.cls for item in application.user_middleware]
    assert SpeechAdmissionMiddleware in middleware_classes


@pytest.mark.anyio
async def test_suffix_adapter_uses_uuid_alias_and_removes_it(tmp_path: Path) -> None:
    source = tmp_path / f"{uuid4()}.audio"
    source.write_bytes(b"RIFF-private-audio")
    observed: list[Path] = []
    marker = object()

    class Inspector:
        async def inspect(
            self,
            path: Path,
            *,
            declared_media_type: str | None,
            deadline: float,
        ) -> object:
            assert deadline > time.monotonic()
            assert declared_media_type == "audio/wav"
            assert path.suffix == ".wav"
            assert path.read_bytes() == b"RIFF-private-audio"
            observed.append(path)
            return marker

    adapter = SuffixAwareAudioInspector(Inspector())
    result = await adapter.inspect(
        source, declared_media_type="audio/wav", deadline=time.monotonic() + 5
    )

    assert result is marker
    assert observed == [source.with_suffix(".wav")]
    assert list(tmp_path.iterdir()) == [source]
