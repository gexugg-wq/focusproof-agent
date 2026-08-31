from __future__ import annotations
# ruff: noqa: E402

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from secrets import token_hex
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Annotated, Any, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from focusproof.config.cost_map import prepare_openhands_cost_map

prepare_openhands_cost_map()

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from focusproof.api.auth import (
    VerifiedIdentity,
    get_verified_identity,
    record_security_audit,
    resolve_verified_identity,
)
from focusproof.api.oidc import (
    IdentityUnavailableError,
    InvalidTokenError,
    OidcTokenVerifier,
    PrincipalResolver,
    configure_token_verifier,
    reset_token_verifier,
)
from focusproof.api.models import (
    CreateSessionRequest,
    SubmitAnswerRequest,
    SubmitEvidenceRequest,
)
from focusproof.config.env import build_speech_capability
from focusproof.config.identity import load_oidc_settings
from focusproof.config.profiles import load_runtime_settings
from focusproof.domain.plugins.base import (
    PublicPluginCapability,
    collect_public_plugin_capabilities,
    normalize_evidence_submission_plugins,
)
from focusproof.domain.plugins.loader import load_evidence_plugin_providers
from focusproof.domain.review import ReviewResult
from focusproof.persistence.database import (
    create_database_engine,
    create_session_factory,
    enforce_safe_database_logging,
)
from focusproof.persistence.security_audit import PersistentSecurityAuditSink
from focusproof.persistence.audit_projection import PersistentAuditProjectionStore
from focusproof.persistence.providers import (
    IdentityStoragePaths,
    PrincipalDisabledError,
    UowEvidenceProvider,
    UowPrincipalResolver,
    select_identity_storage_paths,
)
from focusproof.persistence.repositories import (
    StoredEvidence,
    StoredSession,
)
from focusproof.persistence.schema_check import (
    SchemaOutOfDateError,
    check_schema_revision,
)
from focusproof.openhands_runtime.tool_registry import release_repository_provider
from focusproof.persistence.unit_of_work import UnitOfWork, UnitOfWorkFactory
from focusproof.runtime.evidence import Evidence, LearningGoal, hash_evidence_content
from focusproof.runtime.view import AgentView, SessionView, ToolDescription
from focusproof.recovery import (
    MAINTENANCE_MARKER_NAME,
    RecoveryCoordinationError,
    WriterBlockedError,
    is_recovery_incomplete,
    writer_barrier,
)

if TYPE_CHECKING:
    from focusproof.bootstrap.media_composition import SharedMediaSecurity
    from focusproof.openhands_runtime.factory import LLMFactory
    from focusproof.openhands_runtime.handle import RuntimeMode, RuntimeReviewResult
    from focusproof.openhands_runtime.manager import ConversationManager
    from focusproof.openhands_runtime.runtime_contributions import RuntimeContribution


def _get_openhands_capabilities() -> dict[str, Any]:
    from focusproof.openhands_adapter.capabilities import get_openhands_capabilities

    return get_openhands_capabilities()


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_REQUEST_BODY_BYTES = 262_144
MAINTENANCE_LOCK_NAME = MAINTENANCE_MARKER_NAME
OPERATIONS_LOGGER = logging.getLogger("focusproof.operations")
_OPERATIONAL_FIELDS = frozenset(
    {
        "route",
        "status",
        "latency_ms",
        "provider_calls",
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_cost_microusd",
        "provider_latency_ms",
        "outcome",
    }
)
_IMAGE_EVIDENCE_CAPABILITY: dict[str, Any] = {
    "capabilityId": "image_evidence",
    "enabled": True,
    "formats": ["image/png", "image/jpeg", "image/webp"],
    "maxCount": 4,
    "maxOriginalBytes": 10_485_760,
    "maxNormalizedBytesPerSession": 20_971_520,
    "explanationRequired": True,
}


def _product_capabilities(
    *,
    media_enabled: bool,
    speech_capability: dict[str, Any],
) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    if media_enabled:
        capabilities.append(dict(_IMAGE_EVIDENCE_CAPABILITY))
    capabilities.append(dict(speech_capability))
    return capabilities


def _emit_operational_event(event: str, **fields: str | int | float | bool | None) -> None:
    if set(fields) - _OPERATIONAL_FIELDS:
        raise ValueError("unsupported operational field")
    payload: dict[str, str | int | float | bool | None] = {"event": event}
    payload.update(fields)
    OPERATIONS_LOGGER.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _record_review_operational_signal(
    manager: ConversationManager,
    session_id: str,
    result: RuntimeReviewResult,
    *,
    latency_ms: int,
) -> None:
    usage = manager.get(session_id).provider_usage_snapshot()
    status = (
        result.reviewStatus
        if result.reviewStatus in {"completed", "awaiting_user", "failed"}
        else "failed"
    )
    _emit_operational_event(
        "review",
        status=status,
        latency_ms=latency_ms,
        provider_calls=usage.call_count,
        provider_input_tokens=usage.input_tokens,
        provider_output_tokens=usage.output_tokens,
        provider_cost_microusd=round(usage.cost_usd * 1_000_000),
        provider_latency_ms=round(usage.latency_seconds * 1000),
        outcome="provider_run",
    )


def _record_review_failure_operational_signal(
    *,
    status: str,
    outcome: str,
    latency_ms: int,
) -> None:
    bounded_status = status if status in {"failed", "rejected", "unavailable"} else "failed"
    bounded_outcome = (
        outcome if outcome in {"runtime", "runtime_creation", "provider_admission"} else "runtime"
    )
    _emit_operational_event(
        "review",
        status=bounded_status,
        outcome=bounded_outcome,
        latency_ms=max(0, latency_ms),
    )


def _bounded_route(path: str) -> str:
    parts = path.split("/")
    if len(parts) == 3 and parts[1] == "sessions" and parts[2]:
        return "/sessions/{session_id}"
    if (
        len(parts) == 4
        and parts[1] == "sessions"
        and parts[2]
        and parts[3] in {"evidence", "answer", "review", "events", "reviews"}
    ):
        return f"/sessions/{{session_id}}/{parts[3]}"
    allowed = {"/health", "/ready", "/sessions", "/openhands/capabilities"}
    return path if path in allowed else "unmatched"


class OperationalTelemetryMiddleware:
    def __init__(self, app: ASGIApp, *, data_dir: Path) -> None:
        self._app = app
        self._data_dir = data_dir

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        method = str(scope.get("method", "GET"))
        path = str(scope.get("path", ""))
        route = _bounded_route(path)
        write_window: Any | None = None
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            write_window = writer_barrier(self._data_dir)
            try:
                await asyncio.to_thread(write_window.__enter__)
            except WriterBlockedError as exc:
                _emit_operational_event(
                    "admission_rejection",
                    route=route,
                    status=exc.code,
                    outcome="rejected",
                )
                await JSONResponse(
                    status_code=503,
                    content={"code": exc.code, "retryable": True},
                )(scope, receive, send)
                return
            except RecoveryCoordinationError:
                _emit_operational_event(
                    "admission_rejection",
                    route=route,
                    status="recovery_incomplete",
                    outcome="rejected",
                )
                await JSONResponse(
                    status_code=503,
                    content={"code": "recovery_incomplete", "retryable": True},
                )(scope, receive, send)
                return
        started = monotonic()
        status_code = 500

        async def capture_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, capture_status)
        finally:
            if write_window is not None:
                await asyncio.to_thread(write_window.__exit__, None, None, None)
        latency_ms = max(0, round((monotonic() - started) * 1000))
        _emit_operational_event(
            "request", route=route, status=str(status_code), latency_ms=latency_ms
        )
        if path in {"/health", "/ready"}:
            _emit_operational_event(
                "health",
                route=path,
                status="healthy" if status_code < 500 else "unhealthy",
                outcome="database_runtime",
            )
        if status_code in {401, 403}:
            _emit_operational_event(
                "auth",
                route=route,
                status="invalid" if status_code == 401 else "forbidden",
                outcome="rejected",
            )


def _request_too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"code": "request_too_large", "retryable": False},
    )


def _invalid_token_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"code": "invalid_token", "retryable": False},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden_response() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"code": "forbidden", "retryable": False},
    )


def _identity_unavailable_response(exc: IdentityUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"code": exc.code, "retryable": False},
    )


def _service_unavailable_response(code: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"code": code, "retryable": code != "identity_unavailable"},
    )


def _database_unavailable_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"code": "database_unavailable", "retryable": True},
    )


class ServiceUnavailableError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class IdentityConfigurationError(RuntimeError):
    pass


class RuntimeConfigurationError(RuntimeError):
    pass


class SessionFinalizedError(RuntimeError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session {session_id} is finalized")
        self.session_id = session_id


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, resolver: Any) -> None:
        self._app = app
        self._resolver = resolver

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or scope.get("method") not in {
            "POST",
            "PUT",
            "PATCH",
        }:
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        identity: VerifiedIdentity | None = None
        protected_request = _is_protected_request_scope(scope.get("app"), scope)
        if protected_request:
            try:
                identity = await resolve_verified_identity(
                    request,
                    authorization=_authorization_header_from_scope(scope),
                )
            except InvalidTokenError:
                await _invalid_token_response()(scope, receive, send)
                return
            except PrincipalDisabledError:
                await _forbidden_response()(scope, receive, send)
                return
            except IdentityUnavailableError as exc:
                await _identity_unavailable_response(exc)(scope, receive, send)
                return
            except SQLAlchemyError:
                await _database_unavailable_response()(scope, receive, send)
                return

        max_body_bytes = self._resolver.resolve(scope)
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = 0
            if declared_length > max_body_bytes:
                if protected_request and identity is not None:
                    try:
                        _record_authorized_request(request, identity)
                    except SQLAlchemyError:
                        await _database_unavailable_response()(scope, receive, send)
                        return
                    except IdentityUnavailableError as exc:
                        await _identity_unavailable_response(exc)(scope, receive, send)
                        return
                await _request_too_large_response()(scope, receive, send)
                return

        received_bytes = 0
        request_complete = False
        response_messages: list[Message] = []
        overflow = False

        async def limited_receive() -> Message:
            nonlocal received_bytes, overflow, request_complete
            message = await receive()
            if message["type"] == "http.disconnect":
                request_complete = True
            elif message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                request_complete = not message.get("more_body", False)
                if received_bytes > max_body_bytes:
                    overflow = True
                    request_complete = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def limited_send(message: Message) -> None:
            response_messages.append(message)

        await self._app(scope, limited_receive, limited_send)
        while not request_complete:
            message = await receive()
            if message["type"] == "http.disconnect":
                request_complete = True
                break
            if message["type"] != "http.request":
                continue
            received_bytes += len(message.get("body", b""))
            if received_bytes > max_body_bytes:
                overflow = True
                break
            request_complete = not message.get("more_body", False)
        if overflow:
            await _request_too_large_response()(scope, receive, send)

            return
        for message in response_messages:
            await send(message)


def _authorization_header_from_scope(scope: Scope) -> str | None:
    return Headers(scope=scope).get("authorization")


def _is_protected_request_scope(application: object, scope: Scope) -> bool:
    if not isinstance(application, FastAPI):
        return False
    for route in application.routes:
        if not isinstance(route, APIRoute):
            continue
        match, _ = route.matches(scope)
        if match is not Match.FULL:
            continue
        if _dependant_uses_dependency(route.dependant, get_verified_identity):
            return True
    return False


def _dependant_uses_dependency(dependant: Any, dependency: object) -> bool:
    if getattr(dependant, "call", None) is dependency:
        return True
    return any(
        _dependant_uses_dependency(child, dependency)
        for child in getattr(dependant, "dependencies", ()) or ()
    )


async def _record_protected_validation_failure(
    request: Request,
) -> JSONResponse | None:
    try:
        identity = await resolve_verified_identity(
            request,
            authorization=request.headers.get("authorization"),
        )
        _record_authorized_request(request, identity)
    except InvalidTokenError:
        return _invalid_token_response()
    except PrincipalDisabledError:
        return _forbidden_response()
    except IdentityUnavailableError as exc:
        return _identity_unavailable_response(exc)
    except SQLAlchemyError:
        return _database_unavailable_response()
    return None


def create_app(
    *,
    database_url: str | None = None,
    data_dir: Path | None = None,
    lock_timeout_seconds: float | None = None,
    llm_factory: LLMFactory | None = None,
    principal_resolver: PrincipalResolver | None = None,
    review_timeout_seconds: float = 60.0,
) -> FastAPI:
    configured_profile = os.environ.get("FOCUSPROOF_PROFILE") or "local-dev"
    resolved_data_dir = (
        data_dir
        if data_dir is not None
        else PROJECT_ROOT / (os.environ.get("FOCUSPROOF_DATA_DIR") or "./var")
    ).resolve()
    configured_database_url = database_url or _database_url_from_environment()
    if configured_profile in {"local-dev", "staging", "production"}:
        current_storage = IdentityStoragePaths(
            database_url=configured_database_url,
            conversation_root=resolved_data_dir,
        )
        isolated_storage = _isolated_counterpart_storage(resolved_data_dir)
        selected_storage = select_identity_storage_paths(
            configured_profile,
            anonymous_local_dev=(
                current_storage if configured_profile == "local-dev" else isolated_storage
            ),
            verified=(isolated_storage if configured_profile == "local-dev" else current_storage),
        )
        configured_database_url = selected_storage.database_url
        resolved_data_dir = selected_storage.conversation_root.resolve()
    _validate_database_path(configured_database_url, resolved_data_dir)
    speech_capability = dict(build_speech_capability(os.environ))
    configured_lock_timeout = (
        lock_timeout_seconds
        if lock_timeout_seconds is not None
        else float(os.environ.get("FOCUSPROOF_LOCK_TIMEOUT_SECONDS") or "5")
    )
    effective_llm_factory = llm_factory
    if effective_llm_factory is None and configured_profile == "demo-deterministic":
        effective_llm_factory = demo_deterministic_test_llm
    configured_runtime_mode: RuntimeMode = (
        "openhands-local-scripted-test"
        if effective_llm_factory is not None
        else "openhands-local-real"
    )
    from focusproof.api.speech_admission import SpeechAdmissionGate

    speech_admission_gate = SpeechAdmissionGate()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine: Engine | None = None
        manager: ConversationManager | None = None
        shared_media_security: SharedMediaSecurity | None = None
        speech_provider: Any | None = None
        speech_sweeper: Any | None = None
        speech_registry: Any | None = None
        application.state.readiness_error = None
        application.state.allow_anonymous_identity = False
        effective_speech_capability = dict(speech_capability)
        if effective_speech_capability.get("enabled") is True and not media_enabled:
            effective_speech_capability = {
                "capabilityId": "speech_transcription",
                "schemaVersion": 1,
                "enabled": False,
                "reasonCode": "asr_prerequisites_unavailable",
            }
        application.state.product_capabilities = _product_capabilities(
            media_enabled=media_enabled,
            speech_capability=effective_speech_capability,
        )
        application.state.speech_capability = effective_speech_capability
        application.state.speech_service = None
        application.state.speech_task_registry = None
        speech_admission_gate.close()
        resolved_data_dir.mkdir(parents=True, exist_ok=True)
        try:
            try:
                oidc_settings = load_oidc_settings(
                    os.environ,
                    profile=configured_profile,
                )
            except ValidationError as exc:
                raise IdentityConfigurationError from exc
            application.state.allow_anonymous_identity = (
                not oidc_settings.enabled
                and configured_profile
                in {"local-dev", "demo-deterministic", "demo-real-vision"}
            )
            if not oidc_settings.enabled and not application.state.allow_anonymous_identity:
                application.state.readiness_error = "identity_unavailable"
            engine = create_database_engine(configured_database_url)
            check_schema_revision(engine, PROJECT_ROOT / "alembic.ini")
            uow_factory = UnitOfWorkFactory(create_session_factory(engine))
            if media_enabled:
                from focusproof.bootstrap.media_composition import (
                    compose_shared_media_security,
                )

                shared_media_security = compose_shared_media_security(
                    uow_factory=uow_factory,
                )
                if (
                    effective_speech_capability.get("enabled") is True
                    and not shared_media_security.speech_prerequisites_available
                ):
                    effective_speech_capability = {
                        "capabilityId": "speech_transcription",
                        "schemaVersion": 1,
                        "enabled": False,
                        "reasonCode": "asr_prerequisites_unavailable",
                    }
                    application.state.product_capabilities = _product_capabilities(
                        media_enabled=True,
                        speech_capability=effective_speech_capability,
                    )
                    application.state.speech_capability = effective_speech_capability
            effective_principal_resolver = principal_resolver or UowPrincipalResolver(uow_factory)
            security_audit_sink = (
                PersistentSecurityAuditSink(
                    uow_factory,
                    retention_seconds=oidc_settings.security_audit_retention_seconds,
                )
                if oidc_settings.enabled
                else None
            )
            if security_audit_sink is not None:
                security_audit_sink.sweep_expired(now=datetime.now(UTC))
            token_verifier = (
                OidcTokenVerifier(
                    oidc_settings,
                    principal_resolver=effective_principal_resolver,
                )
                if oidc_settings.enabled
                else None
            )
            configure_token_verifier(token_verifier)
            audit_projection_store = PersistentAuditProjectionStore(uow_factory)
            evidence_provider = UowEvidenceProvider(uow_factory)
            from focusproof.openhands_runtime.locks import FileSessionRunLock
            from focusproof.openhands_runtime.manager import ConversationManager
            from focusproof.openhands_runtime.provider_admission import BoundedProviderAdmission

            run_lock = FileSessionRunLock(
                resolved_data_dir,
                timeout_seconds=configured_lock_timeout,
            )
            plugin_providers = load_evidence_plugin_providers(os.environ)
            try:
                runtime_settings = (
                    load_runtime_settings(os.environ)
                    if effective_llm_factory is None
                    else None
                )
            except ValidationError as exc:
                raise RuntimeConfigurationError from exc
            real_llm_policy = runtime_settings.real_llm if runtime_settings is not None else None
            provider_admission = (
                BoundedProviderAdmission(
                    max_concurrent=real_llm_policy.max_concurrent_reviews,
                    acquire_timeout_seconds=(real_llm_policy.admission_timeout_seconds),
                )
                if real_llm_policy is not None
                else None
            )
            media_runtime_contribution: RuntimeContribution | None = None
            media_content_provider = None
            if media_enabled:
                from focusproof.bootstrap.media_composition import (
                    compose_media_message_content_provider,
                    compose_optional_media_runtime_contribution,
                )

                media_runtime_contribution = compose_optional_media_runtime_contribution(
                    enabled=True,
                    repository=evidence_provider,
                )
                media_content_provider = compose_media_message_content_provider(
                    uow_factory=uow_factory,
                    data_dir=resolved_data_dir,
                )
            runtime_contributions: tuple[RuntimeContribution, ...] = (
                () if media_runtime_contribution is None else (media_runtime_contribution,)
            )
            manager = ConversationManager(
                repository=evidence_provider,
                audit_log=audit_projection_store,
                project_root=PROJECT_ROOT,
                data_dir=resolved_data_dir,
                llm_factory=effective_llm_factory,
                uow_factory=uow_factory,
                run_lock=run_lock,
                review_timeout_seconds=(
                    min(review_timeout_seconds, real_llm_policy.max_review_seconds)
                    if real_llm_policy is not None
                    else review_timeout_seconds
                ),
                provider_admission=provider_admission,
                runtime_settings=runtime_settings,
                media_content_provider=media_content_provider,
                plugin_providers=plugin_providers,
                runtime_contributions=runtime_contributions,
            )
            enforce_safe_database_logging()
            application.state.engine = engine
            application.state.uow_factory = uow_factory
            application.state.security_audit_sink = security_audit_sink
            application.state.security_audit_hmac_key = (
                oidc_settings.principal_fingerprint_key.get_secret_value()
                if oidc_settings.principal_fingerprint_key is not None
                else None
            )
            application.state.audit_projection_store = audit_projection_store
            application.state.evidence_provider = evidence_provider
            application.state.plugin_providers = plugin_providers
            if media_enabled:
                assert shared_media_security is not None
                from focusproof.bootstrap.media_composition import compose_media_command

                application.state.media_ingestion_command = compose_media_command(
                    uow_factory=uow_factory,
                    data_dir=resolved_data_dir,
                    session_run_lock=run_lock,
                    malware_scanner=shared_media_security.malware_scanner,
                    resource_slot_controller=shared_media_security.scan_slots,
                )
                application.state.malware_scanner = shared_media_security.malware_scanner
                application.state.scan_slot_controller = shared_media_security.scan_slots
            if effective_speech_capability.get("enabled") is True:
                assert shared_media_security is not None
                from focusproof.api.speech_admission import (
                    SpeechRecoverySweeper,
                    SpeechTaskRegistry,
                )
                from focusproof.config.env import load_speech_settings
                from focusproof.speech_adapters.dashscope_asr import (
                    DashScopeSpeechTranscriptionProvider,
                )
                from focusproof.speech_adapters.mediainfo_inspector import (
                    MediainfoAudioInspector,
                )
                from focusproof.speech_application import TranscriptionService
                from focusproof.api.speech_routes import SuffixAwareAudioInspector

                settings = load_speech_settings(os.environ)
                if settings is None:
                    raise RuntimeConfigurationError
                uow_factory.configure_speech(
                    active_hmac_key_version=(
                        settings.idempotency_hmac_active_version
                    ),
                    hmac_keys={
                        version: key.encode("utf-8")
                        for version, key in settings.idempotency_hmac_keyring
                    },
                )
                with uow_factory() as speech_uow:
                    readiness_check = getattr(
                        speech_uow.speech_requests, "assert_hmac_readiness", None
                    )
                    if not callable(readiness_check):
                        raise RuntimeConfigurationError
                    readiness_check()
                    speech_uow.resource_slots.reconcile(
                        "asr",
                        configured_count=settings.max_concurrency,
                        config_generation=1,
                    )
                    speech_uow.commit()
                speech_provider = DashScopeSpeechTranscriptionProvider(
                    api_key=settings.api_key
                )
                speech_temp_dir = (resolved_data_dir / "speech" / "temp").resolve()
                application.state.speech_service = TranscriptionService(
                    uow_factory=uow_factory,
                    malware_scanner=shared_media_security.malware_scanner,
                    scan_slots=shared_media_security.scan_slots,
                    audio_inspector=SuffixAwareAudioInspector(
                        MediainfoAudioInspector()
                    ),
                    provider=speech_provider,
                    temp_dir=speech_temp_dir,
                )
                speech_registry = SpeechTaskRegistry()
                speech_sweeper = SpeechRecoverySweeper(
                    uow_factory=uow_factory,
                    temp_dir=speech_temp_dir,
                    stale_after_seconds=125,
                    interval_seconds=30,
                )
                application.state.speech_task_registry = speech_registry
                await speech_sweeper.recover_once()
                await speech_sweeper.start()
                speech_admission_gate.open()
            application.state.plugin_capabilities = collect_public_plugin_capabilities(
                plugin_providers
            )
            application.state.run_lock = run_lock
            application.state.conversation_manager = manager
            from focusproof.openhands_runtime.sdk_contracts import (
                preflight_openhands_sdk_contract,
            )
            preflight_openhands_sdk_contract()
            projection = getattr(manager, "available_tool_names", lambda: ())
            application.state.available_tool_names = tuple(projection())
        except IdentityConfigurationError:
            application.state.readiness_error = "identity_unavailable"
            configure_token_verifier(None)
        except RuntimeConfigurationError:
            application.state.readiness_error = "runtime_unavailable"
            configure_token_verifier(None)
        except SchemaOutOfDateError:
            application.state.readiness_error = "schema_out_of_date"
        except SQLAlchemyError:
            application.state.readiness_error = "database_unavailable"
        except Exception as exc:
            if type(exc).__name__ != "OpenHandsContractUnavailable":
                raise
            application.state.readiness_error = "runtime_contract_unavailable"
        try:
            yield
        finally:
            try:
                speech_admission_gate.close()
                if speech_registry is not None:
                    async def fence_speech() -> None:
                        if speech_sweeper is not None:
                            await speech_sweeper.recover_once()

                    await speech_registry.close(
                        gate=speech_admission_gate,
                        grace_seconds=5.0,
                        fence=fence_speech,
                    )
                if speech_sweeper is not None:
                    await speech_sweeper.close()
                if speech_provider is not None:
                    await speech_provider.aclose()
                if manager is not None:
                    manager.close_all()
            finally:
                reset_token_verifier()
                release_repository_provider()
                if engine is not None:
                    engine.dispose()

    application = FastAPI(title="FocusProof Agent Server", lifespan=lifespan)
    from focusproof.openhands_runtime.locks import SessionBusyError

    application.add_exception_handler(SessionBusyError, _session_busy_handler)

    application.state.recovery_data_dir = resolved_data_dir
    application.state.speech_admission_gate = speech_admission_gate
    media_enabled = os.environ.get("FOCUSPROOF_MEDIA_ENABLED") == "true"
    if media_enabled:
        from focusproof.api.media_routes import build_media_router

        application.include_router(build_media_router())
    from focusproof.api.speech_routes import build_speech_router

    application.include_router(build_speech_router())
    from focusproof.api.request_limits import BodyLimitResolver
    from focusproof.api.speech_admission import SpeechAdmissionMiddleware

    application.add_middleware(
        RequestBodyLimitMiddleware,
        resolver=BodyLimitResolver(application),
    )
    application.add_middleware(
        OperationalTelemetryMiddleware,
        data_dir=resolved_data_dir,
    )
    application.add_middleware(
        SpeechAdmissionMiddleware,
        application=application,
        gate=speech_admission_gate,
    )
    _install_exception_handlers(application)
    _install_routes(application, configured_runtime_mode)
    return application


def demo_deterministic_test_llm(session_id: str) -> Any:
    """Return the demo-deterministic official TestLLM provider."""
    from focusproof.openhands_runtime.demo_deterministic_provider import (
        build_demo_deterministic_test_llm,
    )

    return build_demo_deterministic_test_llm(session_id)


def staging_test_llm(session_id: str) -> Any:
    """Return the staging-only official TestLLM script for one native conversation."""
    from openhands.sdk.llm import Message, MessageToolCall, TextContent
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.testing import TestLLM

    data_dir = Path(os.environ["FOCUSPROOF_DATA_DIR"])
    conversation_id = uuid5(NAMESPACE_URL, f"focusproof:{session_id}")
    persistence_dir = data_dir / "conversations" / session_id / "persistence"
    native_store = Path(LocalConversation.get_persistence_dir(persistence_dir, conversation_id))
    # SDK 1.31.0 exposes get_persistence_dir but no public restored-state
    # predicate. This staging-only TestLLM selection never consults SQL state.
    restoring_native_conversation = (native_store / "base_state.json").is_file()
    learner_input_call = MessageToolCall(
        id="call_staging_learner_input",
        name="focusproof_learner_input",
        arguments=json.dumps(
            {
                "question": "Explain why native event continuity matters after restart.",
                "reason": "Confirm learner understanding after durable recovery.",
                "requested_evidence_type": "text",
            }
        ),
        origin="completion",
    )
    draft_call = MessageToolCall(
        id="call_staging_review_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Evidence is repository-backed."],
                "understanding_findings": [
                    "The learner explains that durable IDs survive restart."
                ],
                "contradictions": [],
                "recommended_next_step": "Add one concrete replay example.",
                "confidence": 0.8,
            }
        ),
        origin="completion",
    )
    learner_input_message = Message(
        role="assistant",
        content=[TextContent(text="Ask for learner confirmation")],
        tool_calls=[learner_input_call],
    )
    review_draft_message = Message(
        role="assistant",
        content=[TextContent(text="Submit the staging review draft")],
        tool_calls=[draft_call],
    )
    messages: list[Message | Exception] = (
        [review_draft_message]
        if restoring_native_conversation
        else [learner_input_message, review_draft_message]
    )
    return TestLLM.from_messages(messages)


def create_staging_test_app() -> FastAPI:
    """Create the credential-free staging proof app with the official SDK TestLLM."""
    if os.environ.get("FOCUSPROOF_PROFILE") != "staging":
        raise RuntimeError("staging TestLLM app requires FOCUSPROOF_PROFILE=staging")

    return create_app(llm_factory=staging_test_llm)


def _validate_database_path(database_url: str, data_dir: Path) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return

    database = url.database
    if database is None or database == ":memory:":
        return

    database_path = Path(database)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    if not database_path.resolve().is_relative_to(data_dir):
        raise ValueError("SQLite database path must be inside FOCUSPROOF_DATA_DIR")


async def _session_busy_handler(request: Request, exc: Any) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=409,
        content={
            "code": "session_busy",
            "sessionId": exc.session_id,
            "retryable": True,
        },
    )


def _install_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(ServiceUnavailableError)
    async def service_unavailable_handler(
        request: Request,
        exc: ServiceUnavailableError,
    ) -> JSONResponse:
        del request
        return _service_unavailable_response(exc.code)

    @application.exception_handler(IdentityUnavailableError)
    async def identity_unavailable_handler(
        request: Request,
        exc: IdentityUnavailableError,
    ) -> JSONResponse:
        del request
        return _identity_unavailable_response(exc)

    @application.exception_handler(InvalidTokenError)
    async def invalid_token_handler(
        request: Request,
        exc: InvalidTokenError,
    ) -> JSONResponse:
        del request, exc
        return _invalid_token_response()

    @application.exception_handler(PrincipalDisabledError)
    async def principal_disabled_handler(
        request: Request,
        exc: PrincipalDisabledError,
    ) -> JSONResponse:
        del request, exc
        return _forbidden_response()

    @application.exception_handler(SessionFinalizedError)
    async def session_finalized_handler(
        request: Request,
        exc: SessionFinalizedError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=409,
            content={
                "code": "session_finalized",
                "sessionId": exc.session_id,
                "retryable": False,
            },
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error_handler(
        request: Request,
        exc: SQLAlchemyError,
    ) -> JSONResponse:
        del request, exc
        return _database_unavailable_response()

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        if _is_protected_request_scope(request.app, request.scope):
            audit_response = await _record_protected_validation_failure(request)
            if audit_response is not None:
                return audit_response
        return await request_validation_exception_handler(request, exc)


def _install_routes(
    application: FastAPI,
    configured_runtime_mode: RuntimeMode,
) -> None:
    @application.get("/health")
    def health(request: Request) -> dict[str, Any]:
        readiness = getattr(request.app.state, "readiness_error", None)
        return {
            "status": "ok" if readiness is None else "degraded",
            "project": "focusproof-agent",
            "openhands": _get_openhands_capabilities(),
            "readiness": readiness,
        }

    @application.get("/ready")
    def ready(request: Request) -> Any:
        try:
            recovery_incomplete = is_recovery_incomplete(Path(request.app.state.recovery_data_dir))
        except RecoveryCoordinationError:
            recovery_incomplete = True
        if recovery_incomplete:
            return JSONResponse(
                status_code=503,
                content={"code": "recovery_incomplete", "retryable": True},
            )
        readiness_error = getattr(
            request.app.state,
            "readiness_error",
            "database_unavailable",
        )
        if readiness_error is not None:
            return _service_unavailable_response(readiness_error)
        engine = getattr(request.app.state, "engine", None)
        manager = getattr(request.app.state, "conversation_manager", None)
        if engine is None or manager is None:
            return JSONResponse(
                status_code=503,
                content={"code": "database_unavailable", "retryable": True},
            )
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(
                status_code=503,
                content={"code": "database_unavailable", "retryable": True},
            )
        return {"status": "ready"}

    @application.get("/openhands/capabilities")
    def openhands_capabilities() -> dict[str, Any]:
        return _get_openhands_capabilities()

    @application.post("/sessions")
    def create_session(
        body: CreateSessionRequest,
        http_request: Request,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
        manager: Annotated[Any, Depends(get_conversation_manager)],
    ) -> dict[str, str]:
        _record_authorized_request(http_request, identity)
        session_id = f"sess_{uuid4().hex}"
        conversation_id = uuid5(NAMESPACE_URL, f"focusproof:{session_id}").hex
        now = datetime.now(UTC)
        runtime_mode = configured_runtime_mode
        record = StoredSession(
            session_id=session_id,
            owner_user_id=identity.verified_user_id,
            status="running",
            adapter_mode=runtime_mode,
            domain=body.domain,
            title=body.title,
            goal=body.goal,
            expected_output=body.expectedOutput,
            planned_minutes=body.plannedMinutes,
            conversation_id=conversation_id,
            runtime_mode=runtime_mode,
            review_result=None,
            goal_conversation_synced_at=None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        with uow_factory() as uow:
            uow.sessions.create(record)
            uow.audit_events.append(
                session_id,
                "session.created",
                "system",
                {"status": "running"},
                source_openhands_event_id=None,
                event_id=f"evt_session_created_{session_id}",
            )
            uow.commit()
        from focusproof.openhands_runtime.factory import (
            RuntimeCreationError,
            RuntimeUnavailableError,
        )

        try:
            manager.get_or_restore(session_id, identity.verified_user_id)
        except (RuntimeUnavailableError, RuntimeCreationError, ValueError):
            pass
        return {"sessionId": session_id, "status": "running"}

    @application.post("/sessions/{session_id}/evidence")
    def submit_evidence(
        session_id: str,
        body: SubmitEvidenceRequest,
        http_request: Request,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
        manager: Annotated[Any, Depends(get_conversation_manager)],
        run_lock: Annotated[Any, Depends(get_session_run_lock)],
    ) -> dict[str, str | bool]:
        providers = tuple(getattr(http_request.app.state, "plugin_providers", ()))
        normalized_body = cast(
            SubmitEvidenceRequest,
            normalize_evidence_submission_plugins(body, providers=providers),
        )
        evidence_id = _evidence_id_for_request(session_id, normalized_body)
        record = StoredEvidence(
            evidence_id=evidence_id,
            session_id=session_id,
            evidence_type=normalized_body.evidenceType,
            content_hash=hash_evidence_content(
                normalized_body.textContent, normalized_body.sourceUrl
            ),
            text_content=normalized_body.textContent,
            source_url=normalized_body.sourceUrl,
            metadata=normalized_body.metadata,
            conversation_synced_at=None,
            created_at=datetime.now(UTC),
        )
        reviewed_replay = False
        with run_lock.acquire(session_id):
            _owned_session_or_audit_not_found(
                uow_factory,
                session_id,
                identity.verified_user_id,
                http_request,
                identity,
            )
            _record_authorized_request(http_request, identity)
            with uow_factory() as uow:
                session = _owned_session_in_uow(
                    uow,
                    session_id,
                    identity.verified_user_id,
                )
                existing = uow.evidence.get(session_id, evidence_id)
                if session.status == "reviewed":
                    if existing is None:
                        raise SessionFinalizedError(session_id)
                    reviewed_replay = True
                elif existing is None:
                    uow.evidence.add(record)
                    uow.commit()
        sync_pending = False
        if not reviewed_replay:
            from focusproof.openhands_runtime.factory import (
                RuntimeCreationError,
                RuntimeUnavailableError,
            )
            from focusproof.openhands_runtime.locks import SessionBusyError

            try:
                manager.send_evidence(session_id, identity.verified_user_id)
            except (
                SessionBusyError,
                RuntimeUnavailableError,
                RuntimeCreationError,
                ValueError,
            ):
                sync_pending = True
        return {
            "evidenceId": evidence_id,
            "sessionId": session_id,
            "syncPending": sync_pending,
        }

    @application.post("/sessions/{session_id}/answer")
    def submit_answer(
        session_id: str,
        body: SubmitAnswerRequest,
        http_request: Request,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
        manager: Annotated[Any, Depends(get_conversation_manager)],
        run_lock: Annotated[Any, Depends(get_session_run_lock)],
    ) -> dict[str, str | bool]:
        reviewed_replay = False
        with run_lock.acquire(session_id):
            _owned_session_or_audit_not_found(
                uow_factory,
                session_id,
                identity.verified_user_id,
                http_request,
                identity,
            )
            _record_authorized_request(http_request, identity)
            with uow_factory() as uow:
                session = _owned_session_in_uow(
                    uow,
                    session_id,
                    identity.verified_user_id,
                )
                existing = uow.answers.get(session_id, body.questionId)
                if session.status == "reviewed":
                    if existing is None or existing.answer != body.answer:
                        raise SessionFinalizedError(session_id)
                    reviewed_replay = True
                else:
                    uow.answers.upsert(
                        session_id,
                        body.questionId,
                        body.answer,
                    )
                    uow.commit()
        sync_pending = False
        if not reviewed_replay:
            from focusproof.openhands_runtime.factory import (
                RuntimeCreationError,
                RuntimeUnavailableError,
            )
            from focusproof.openhands_runtime.locks import SessionBusyError

            try:
                manager.send_answer(session_id, identity.verified_user_id)
            except (
                SessionBusyError,
                RuntimeUnavailableError,
                RuntimeCreationError,
                ValueError,
            ):
                sync_pending = True
        return {
            "sessionId": session_id,
            "questionId": body.questionId,
            "syncPending": sync_pending,
        }

    @application.post("/sessions/{session_id}/review", response_model=None)
    async def review_session(
        session_id: str,
        request: Request,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
        manager: Annotated[Any, Depends(get_conversation_manager)],
    ) -> dict[str, Any] | JSONResponse:
        _owned_session_or_audit_not_found(
            uow_factory,
            session_id,
            identity.verified_user_id,
            request,
            identity,
        )
        _record_authorized_request(request, identity)
        from focusproof.openhands_runtime.factory import (
            ProviderInfrastructureUnavailableError,
            RuntimeCreationError,
            RuntimeUnavailableError,
        )
        from focusproof.openhands_runtime.provider_admission import (
            ProviderAdmissionUnavailableError,
        )

        await request.body()
        review_started = monotonic()
        review_call_id = token_hex(16)
        review_task = asyncio.create_task(
            asyncio.to_thread(
                manager.run_review,
                session_id,
                identity.verified_user_id,
                review_call_id,
            )
        )
        try:
            while not review_task.done():
                if await request.is_disconnected():
                    manager.interrupt(session_id, review_call_id)
                    try:
                        await review_task
                    finally:
                        raise asyncio.CancelledError
                await asyncio.sleep(0.01)
            result = await review_task
        except asyncio.CancelledError:
            manager.interrupt(session_id, review_call_id)
            raise
        except ProviderInfrastructureUnavailableError:
            _record_review_failure_operational_signal(
                status="unavailable",
                outcome="runtime",
                latency_ms=max(0, round((monotonic() - review_started) * 1000)),
            )
            return _service_unavailable_response("runtime_unavailable")
        except RuntimeUnavailableError as exc:
            _record_review_failure_operational_signal(
                status=(
                    "rejected"
                    if isinstance(exc, ProviderAdmissionUnavailableError)
                    else "unavailable"
                ),
                outcome=(
                    "provider_admission"
                    if isinstance(exc, ProviderAdmissionUnavailableError)
                    else "runtime"
                ),
                latency_ms=max(0, round((monotonic() - review_started) * 1000)),
            )
            return _runtime_unavailable(session_id, "unavailable")
        except (RuntimeCreationError, ValueError):
            _record_review_failure_operational_signal(
                status="failed",
                outcome="runtime_creation",
                latency_ms=max(0, round((monotonic() - review_started) * 1000)),
            )
            return _runtime_unavailable(session_id, "failed")
        _record_review_operational_signal(
            manager,
            session_id,
            result,
            latency_ms=max(0, round((monotonic() - review_started) * 1000)),
        )
        if result.reviewStatus == "failed":
            return JSONResponse(status_code=503, content=result.model_dump(mode="json"))
        with uow_factory() as uow:
            session = uow.sessions.get(session_id)
            latest = uow.audit_events.latest(session_id)
            events_count = len(uow.audit_events.list(session_id))
        response = cast(dict[str, Any], result.model_dump())
        response.update(
            {
                "status": session.status if session is not None else result.reviewStatus,
                "latestEventType": latest.type if latest is not None else None,
                "eventsCount": events_count,
            }
        )
        return response

    @application.get("/sessions/{session_id}")
    def get_session(
        session_id: str,
        request: Request,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    ) -> dict[str, Any]:
        session = _owned_session_or_audit_not_found(
            uow_factory,
            session_id,
            identity.verified_user_id,
            request,
            identity,
        )
        _record_authorized_request(request, identity)
        with uow_factory() as uow:
            evidence = uow.evidence.list_for_session(session_id)
            answers = uow.answers.list_for_session(session_id)
        goal = _goal(session)
        runtime_evidence = [_runtime_evidence(item) for item in evidence]
        plugin_capabilities = list(getattr(request.app.state, "plugin_capabilities", ()))
        review = (
            ReviewResult.model_validate(session.review_result)
            if session.status == "reviewed" and session.review_result is not None
            else None
        )
        return {
            "sessionId": session_id,
            "state": {
                "sessionId": session_id,
                "ownerUserId": session.owner_user_id,
                "status": session.status,
                "goal": goal.model_dump(mode="json"),
                "evidence": [item.model_dump(mode="json") for item in runtime_evidence],
                "answers": {item.question_id: item.answer for item in answers},
                "observations": [],
                "previousActions": [],
                "reviewResult": review.model_dump(mode="json") if review else None,
                "adapterMode": session.adapter_mode,
                "conversationId": session.conversation_id,
                "runtimeMode": session.runtime_mode,
            },
            "view": _view(
                session_id,
                session.status,
                goal,
                runtime_evidence,
                review,
                plugin_capabilities,
                available_tool_names=tuple(getattr(request.app.state, "available_tool_names", ())),
                product_capabilities=list(getattr(request.app.state, "product_capabilities", ())),
            ),
        }

    @application.get("/sessions/{session_id}/events")
    def get_events(
        session_id: str,
        request: Request,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    ) -> dict[str, list[dict[str, Any]]]:
        _owned_session_or_audit_not_found(
            uow_factory,
            session_id,
            identity.verified_user_id,
            request,
            identity,
        )
        _record_authorized_request(request, identity)
        with uow_factory() as uow:
            events = uow.audit_events.list(session_id)
        return {
            "events": [
                {
                    "id": event.event_id,
                    "sessionId": event.session_id,
                    "type": event.type,
                    "sequence": event.sequence,
                    "createdAt": event.created_at.isoformat(),
                    "actor": event.actor,
                    "payload": event.payload,
                }
                for event in events
            ]
        }

    @application.get("/sessions/{session_id}/reviews")
    def get_reviews(
        session_id: str,
        request: Request,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    ) -> dict[str, list[dict[str, Any]]]:
        _owned_session_or_audit_not_found(
            uow_factory,
            session_id,
            identity.verified_user_id,
            request,
            identity,
        )
        _record_authorized_request(request, identity)
        with uow_factory() as uow:
            reviews = uow.reviews.list_for_session(session_id)
        return {
            "reviews": [
                {
                    "reviewId": review.review_id,
                    "sessionId": review.session_id,
                    "conversationId": review.conversation_id,
                    "reviewStatus": review.review_status,
                    "score": review.score,
                    "result": review.result,
                    "nativeEventCount": review.native_event_count,
                    "sourceOpenHandsEventId": review.source_openhands_event_id,
                    "createdAt": review.created_at.isoformat(),
                }
                for review in reviews
            ]
        }


def get_uow_factory(request: Request) -> UnitOfWorkFactory:
    _require_ready(request)
    return cast(UnitOfWorkFactory, request.app.state.uow_factory)


def get_conversation_manager(request: Request) -> Any:
    _require_ready(request)
    return request.app.state.conversation_manager


def get_session_run_lock(request: Request) -> Any:
    _require_ready(request)
    return request.app.state.run_lock


def get_audit_projection_store(request: Request) -> PersistentAuditProjectionStore:
    _require_ready(request)
    return cast(
        PersistentAuditProjectionStore,
        request.app.state.audit_projection_store,
    )


def get_session_repository(request: Request) -> UowEvidenceProvider:
    _require_ready(request)
    return cast(UowEvidenceProvider, request.app.state.evidence_provider)


def _require_ready(request: Request) -> None:
    code = getattr(request.app.state, "readiness_error", "database_unavailable")
    if code is not None:
        raise ServiceUnavailableError(code)


def _database_url_from_environment() -> str:
    return os.environ.get("DATABASE_URL") or ("sqlite+pysqlite:///./var/focusproof.db")


def _isolated_counterpart_storage(data_dir: Path) -> IdentityStoragePaths:
    counterpart_root = data_dir.parent / f"{data_dir.name}-identity-isolated"
    return IdentityStoragePaths(
        database_url=(
            f"sqlite+pysqlite:///{counterpart_root / 'focusproof-identity-isolated.sqlite3'}"
        ),
        conversation_root=counterpart_root,
    )


def _owned_session(
    uow_factory: UnitOfWorkFactory,
    session_id: str,
    verified_user_id: str,
) -> StoredSession:
    with uow_factory() as uow:
        session = uow.sessions.get(session_id)
    if session is None or session.owner_user_id != verified_user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _owned_session_or_audit_not_found(
    uow_factory: UnitOfWorkFactory,
    session_id: str,
    verified_user_id: str,
    request: Request,
    identity: VerifiedIdentity,
) -> StoredSession:
    with uow_factory() as uow:
        session = uow.sessions.get(session_id)
    if session is None or session.owner_user_id != verified_user_id:
        record_security_audit(
            request,
            principal_id=identity.verified_user_id,
            token_fingerprint=identity.token_fingerprint,
            outcome="failure",
            reason_category="not_found",
        )
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _record_authorized_request(
    request: Request,
    identity: VerifiedIdentity,
) -> None:
    record_security_audit(
        request,
        principal_id=identity.verified_user_id,
        token_fingerprint=identity.token_fingerprint,
        outcome="success",
        reason_category="success",
    )


def _owned_session_in_uow(
    uow: UnitOfWork,
    session_id: str,
    verified_user_id: str,
) -> StoredSession:
    session = uow.sessions.get(session_id)
    if session is None or session.owner_user_id != verified_user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _goal(session: StoredSession) -> LearningGoal:
    return LearningGoal(
        domain=session.domain,
        title=session.title,
        goal=session.goal,
        expectedOutput=session.expected_output,
        plannedMinutes=session.planned_minutes,
    )


def _runtime_evidence(stored: StoredEvidence) -> Evidence:
    return Evidence(
        evidenceId=stored.evidence_id,
        evidenceType=stored.evidence_type,
        contentHash=stored.content_hash,
        textContent=stored.text_content,
        sourceUrl=stored.source_url,
        metadata=stored.metadata,
    )


def _evidence_id_for_request(
    session_id: str,
    request: SubmitEvidenceRequest,
) -> str:
    identity = json.dumps(
        {
            "session_id": session_id,
            "evidence_type": request.evidenceType,
            "text_content": request.textContent,
            "source_url": request.sourceUrl,
            "metadata": request.metadata,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"ev_{sha256(identity.encode('utf-8')).hexdigest()[:48]}"


def _view(
    session_id: str,
    status: str,
    goal: LearningGoal,
    evidence: list[Evidence],
    review: ReviewResult | None,
    plugin_capabilities: list[PublicPluginCapability | dict[str, Any]],
    *,
    available_tool_names: tuple[str, ...] = (),
    product_capabilities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return AgentView(
        session=SessionView(id=session_id, status=status),
        goal=goal,
        evidence=evidence,
        verificationResults=[],
        findings=review.findings if review else [],
        unansweredQuestions=[],
        availableTools=[
            ToolDescription(
                name=name,
                description="Runtime tool available for this session.",
                inputSchema={"type": "object", "properties": {}},
            )
            for name in available_tool_names
        ],
        previousActions=[],
        pluginCapabilities=[
            item
            if isinstance(item, dict)
            else {
                "pluginId": item.plugin_id,
                "capabilityId": item.capability_id,
                "enabled": item.enabled,
                "metadata": dict(item.metadata),
            }
            for item in plugin_capabilities
        ],
        productCapabilities=product_capabilities or [],
    ).model_dump(mode="json")


def _runtime_unavailable(session_id: str, mode: RuntimeMode) -> JSONResponse:
    from focusproof.openhands_runtime.handle import RuntimeReviewResult

    result = RuntimeReviewResult(
        sessionId=session_id,
        conversationMode=mode,
        usedOpenHandsConversation=False,
        reviewStatus="failed",
        error="OpenHands runtime is unavailable",
    )
    return JSONResponse(status_code=503, content=result.model_dump(mode="json"))


app = create_app()
