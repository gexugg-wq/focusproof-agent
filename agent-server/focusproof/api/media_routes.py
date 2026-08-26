from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from focusproof.api.auth import VerifiedIdentity, get_verified_identity
from focusproof.api.media_models import MediaEvidenceResponse
from focusproof.api.request_limits import MEDIA_UPLOAD_ROUTE_NAME, MediaUploadRoute
from focusproof.media_core.limits import MediaQuotaExceeded
from focusproof.media_application import (
    MediaDisabledError,
    MediaMaliciousError,
    MediaScanUnavailableError,
    MediaSourceTooLargeError,
    ThreadSafeMediaCancellationGate,
    UnsupportedMediaError,
)
from focusproof.persistence.repositories import (
    IdempotencyConflictError,
    MediaAuthorizationError,
    MediaQuotaExceededError,
)

_SAFE_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._:-]{1,255}\Z")


class MediaCommand(Protocol):
    def execute(
        self,
        *,
        owner_id: str,
        session_id: str,
        stream: Any,
        declared_media_type: str | None,
        explanation: str,
        idempotency_key: str,
        cancellation_gate: object | None = None,
    ) -> object: ...


def _safe_error(status: int, code: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "retryable": retryable})


def _command_result(value: object) -> tuple[Any, bool]:
    result = getattr(value, "result", value)
    replayed = bool(getattr(value, "replayed", False))
    return result, replayed


def build_media_router(command: MediaCommand | None = None) -> APIRouter:
    router = APIRouter(route_class=MediaUploadRoute)

    @router.post(
        "/sessions/{session_id}/evidence/image",
        name=MEDIA_UPLOAD_ROUTE_NAME,
        response_model=MediaEvidenceResponse,
    )
    async def upload_image_evidence(
        session_id: str,
        request: Request,
        file: Annotated[UploadFile, File()],
        explanation: Annotated[str, Form()],
        idempotency_key: Annotated[str, Form()],
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
    ) -> MediaEvidenceResponse | JSONResponse:
        normalized_explanation = explanation.strip()
        if not normalized_explanation:
            return _safe_error(422, "explanation_required")
        if _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None:
            return _safe_error(422, "invalid_idempotency_key")
        selected_command = command or getattr(request.app.state, "media_ingestion_command", None)
        if selected_command is None:
            return _safe_error(503, "media_unavailable", retryable=True)
        try:
            cancellation_gate = ThreadSafeMediaCancellationGate()
            worker = asyncio.create_task(
                asyncio.to_thread(
                    selected_command.execute,
                    owner_id=identity.verified_user_id,
                    session_id=session_id,
                    stream=file.file,
                    declared_media_type=file.content_type,
                    explanation=normalized_explanation,
                    idempotency_key=idempotency_key,
                    cancellation_gate=cancellation_gate,
                )
            )
            try:
                outcome = await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancellation_won = cancellation_gate.cancel()
                if cancellation_won:
                    try:
                        await worker
                    except BaseException:
                        pass
                    raise
                outcome = await worker
            result, replayed = _command_result(outcome)
            return MediaEvidenceResponse(
                evidenceId=result.evidence_id,
                mediaType=result.media_type,
                normalizedBytes=result.byte_size,
                replayed=replayed,
            )
        except MediaDisabledError:
            return _safe_error(503, "media_disabled")
        except MediaMaliciousError:
            return _safe_error(422, "media_malicious")
        except MediaScanUnavailableError:
            return _safe_error(503, "media_scan_unavailable", retryable=True)
        except MediaAuthorizationError:
            return _safe_error(404, "media_session_unavailable")
        except UnsupportedMediaError:
            return _safe_error(415, "unsupported_media")
        except MediaSourceTooLargeError:
            return _safe_error(413, "media_too_large")
        except IdempotencyConflictError:
            return _safe_error(409, "idempotency_conflict")
        except (MediaQuotaExceededError, MediaQuotaExceeded):
            return _safe_error(409, "media_quota_exceeded")
        except Exception:
            return _safe_error(500, "media_ingestion_failed", retryable=True)
        finally:
            await file.close()

    return router
