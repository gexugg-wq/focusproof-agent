from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Protocol, cast

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from focusproof.api.request_limits import (
    SPEECH_UPLOAD_ROUTE_NAME,
    SpeechUploadRoute,
)
from focusproof.api.speech_models import (
    SpeechTranscriptionResponse,
    speech_error_http,
)
from focusproof.speech_application import (
    SpeechExecutionAdmission,
    TranscriptionService,
    UploadedSpeechFile,
)
from focusproof.speech_core.errors import SpeechError, SpeechErrorCode
from focusproof.speech_core.models import LanguageHint, MAX_AUDIO_BYTES

_CHUNK_BYTES = 64 * 1024


class SpeechService(Protocol):
    async def execute(
        self,
        admission: SpeechExecutionAdmission,
        upload: Any,
        language_hint: LanguageHint,
        disconnect_probe: Any,
    ) -> Any: ...


def _error(status: int, code: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": code, "retryable": retryable},
    )



class SuffixAwareAudioInspector:
    _SUFFIXES = {
        "audio/webm;codecs=opus": ".webm",
        "audio/wav": ".wav",
        "audio/mpeg": ".mp3",
    }

    def __init__(self, inspector: Any) -> None:
        self._inspector = inspector

    async def inspect(
        self,
        path: Path,
        *,
        declared_media_type: str | None,
        deadline: float,
    ) -> Any:
        suffix = (
            self._SUFFIXES.get(declared_media_type)
            if declared_media_type is not None
            else None
        )
        if suffix is None:
            return await self._inspector.inspect(
                path,
                declared_media_type=declared_media_type,
                deadline=deadline,
            )
        alias = path.with_suffix(suffix)
        await asyncio.to_thread(alias.hardlink_to, path)
        try:
            return await self._inspector.inspect(
                alias,
                declared_media_type=declared_media_type,
                deadline=deadline,
            )

        finally:
            await asyncio.shield(asyncio.to_thread(alias.unlink, missing_ok=True))


class StreamingSpeechUpload:
    def __init__(self, upload: UploadFile) -> None:
        self._upload = upload
        self.declared_media_type = upload.content_type

    async def write_to(
        self,
        destination: Path,
        *,
        deadline: float,
    ) -> UploadedSpeechFile:
        digest = sha256()
        byte_size = 0
        handle = await asyncio.to_thread(destination.open, "xb")
        try:
            while True:
                async with asyncio.timeout_at(deadline):
                    chunk = await self._upload.read(_CHUNK_BYTES)
                if not chunk:
                    break
                byte_size += len(chunk)
                if byte_size > MAX_AUDIO_BYTES:
                    raise SpeechError(SpeechErrorCode.AUDIO_TOO_LARGE)
                digest.update(chunk)
                await asyncio.to_thread(handle.write, chunk)
            if byte_size == 0:
                raise SpeechError(SpeechErrorCode.INVALID_AUDIO)
        finally:
            await asyncio.to_thread(handle.close)
        return UploadedSpeechFile(
            byte_size=byte_size,
            streaming_sha256=digest.hexdigest(),
        )


def build_speech_router() -> APIRouter:
    router = APIRouter(route_class=SpeechUploadRoute)

    @router.post(
        "/sessions/{session_id}/transcriptions",
        name=SPEECH_UPLOAD_ROUTE_NAME,
        response_model=SpeechTranscriptionResponse,
    )
    async def create_speech_transcription(
        session_id: str,
        request: Request,
        files: Annotated[list[UploadFile] | None, File(alias="file")] = None,
        language_hint_value: Annotated[str, Form(alias="languageHint")] = "auto",
    ) -> SpeechTranscriptionResponse | JSONResponse:
        del session_id
        capability = getattr(request.app.state, "speech_capability", {"enabled": False})
        if capability.get("enabled") is not True:
            return _error(503, "speech_disabled")
        form = await request.form()
        parsed_uploads = [
            (name, value)
            for name, value in form.multi_items()
            if isinstance(value, StarletteUploadFile)
        ]
        if (
            files is None
            or len(files) != 1
            or len(parsed_uploads) != 1
            or parsed_uploads[0][0] != "file"
        ):
            for _, item in parsed_uploads:
                await item.close()
            return _error(422, "one_audio_file_required")
        upload_file = files[0]
        try:
            raw_language = form.get("languageHint")
            if raw_language is None:
                raw_language = language_hint_value
            if not isinstance(raw_language, str):
                return _error(422, "invalid_language_hint")
            try:
                language_hint = LanguageHint(raw_language)
            except ValueError:
                return _error(422, "invalid_language_hint")
            admission = getattr(request.state, "speech_admission", None)
            if not isinstance(admission, SpeechExecutionAdmission):
                return _error(503, "transcription_provider_unavailable", retryable=True)
            service = getattr(request.app.state, "speech_service", None)
            if service is None:
                return _error(503, "transcription_provider_unavailable", retryable=True)
            result = await cast(TranscriptionService, service).execute(
                admission,
                StreamingSpeechUpload(upload_file),
                language_hint,
                request.is_disconnected,
            )
            return SpeechTranscriptionResponse(
                requestId=str(result.request_id),
                transcript=result.transcript,
                provider=result.provider,
                model=result.model,
            )
        except SpeechError as exc:
            status, retryable = speech_error_http(exc.code)
            return _error(status, exc.code.value, retryable=retryable)
        except Exception:
            return _error(500, SpeechErrorCode.TRANSCRIPTION_FAILED.value, retryable=True)
        finally:
            await upload_file.close()

    return router
