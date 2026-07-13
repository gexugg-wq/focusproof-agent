from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from focusproof.api.auth import VerifiedIdentity, get_verified_identity
from focusproof.api.models import (
    CreateSessionRequest,
    DebugConversationTestRequest,
    SubmitAnswerRequest,
    SubmitEvidenceRequest,
)
from focusproof.config.env import get_env_status
from focusproof.domain.review import ReviewResult
from focusproof.openhands_adapter import real_conversation
from focusproof.openhands_adapter.capabilities import get_openhands_capabilities
from focusproof.openhands_adapter.llm_config import get_llm_config_status
from focusproof.openhands_runtime.factory import (
    LLMFactory,
    RuntimeCreationError,
    RuntimeUnavailableError,
)
from focusproof.openhands_runtime.handle import RuntimeMode, RuntimeReviewResult
from focusproof.openhands_runtime.locks import (
    FileSessionRunLock,
    SessionBusyError,
)
from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.openhands_runtime.tool_registry import release_repository_provider
from focusproof.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from focusproof.persistence.event_log import PersistentAuditEventLog
from focusproof.persistence.providers import UowEvidenceProvider
from focusproof.persistence.repositories import (
    StoredEvidence,
    StoredSession,
)
from focusproof.persistence.schema_check import (
    SchemaOutOfDateError,
    check_schema_revision,
)
from focusproof.persistence.unit_of_work import UnitOfWorkFactory
from focusproof.runtime.evidence import Evidence, LearningGoal, hash_evidence_content
from focusproof.runtime.view import AgentView, SessionView, ToolDescription

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class ServiceUnavailableError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def create_app(
    *,
    database_url: str | None = None,
    data_dir: Path | None = None,
    lock_timeout_seconds: float | None = None,
    llm_factory: LLMFactory | None = None,
) -> FastAPI:
    resolved_data_dir = (
        data_dir
        if data_dir is not None
        else PROJECT_ROOT / (os.environ.get("FOCUSPROOF_DATA_DIR") or "./var")
    ).resolve()
    configured_database_url = database_url or _database_url_from_environment()
    _validate_database_path(configured_database_url, resolved_data_dir)
    configured_lock_timeout = (
        lock_timeout_seconds
        if lock_timeout_seconds is not None
        else float(os.environ.get("FOCUSPROOF_LOCK_TIMEOUT_SECONDS") or "5")
    )
    configured_runtime_mode: RuntimeMode = (
        "openhands-local-scripted-test"
        if llm_factory is not None
        else "openhands-local-real"
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine: Engine | None = None
        manager: ConversationManager | None = None
        application.state.readiness_error = None
        resolved_data_dir.mkdir(parents=True, exist_ok=True)
        try:
            engine = create_database_engine(configured_database_url)
            check_schema_revision(engine, PROJECT_ROOT / "alembic.ini")
            uow_factory = UnitOfWorkFactory(create_session_factory(engine))
            audit_log = PersistentAuditEventLog(uow_factory)
            evidence_provider = UowEvidenceProvider(uow_factory)
            run_lock = FileSessionRunLock(
                resolved_data_dir,
                timeout_seconds=configured_lock_timeout,
            )
            manager = ConversationManager(
                repository=evidence_provider,
                audit_log=audit_log,
                project_root=PROJECT_ROOT,
                data_dir=resolved_data_dir,
                llm_factory=llm_factory,
                uow_factory=uow_factory,
                run_lock=run_lock,
            )
            application.state.engine = engine
            application.state.uow_factory = uow_factory
            application.state.audit_log = audit_log
            application.state.evidence_provider = evidence_provider
            application.state.run_lock = run_lock
            application.state.conversation_manager = manager
        except SchemaOutOfDateError:
            application.state.readiness_error = "schema_out_of_date"
        except SQLAlchemyError:
            application.state.readiness_error = "database_unavailable"
        try:
            yield
        finally:
            if manager is not None:
                manager.close_all()
            release_repository_provider()
            if engine is not None:
                engine.dispose()

    application = FastAPI(title="FocusProof Agent Server", lifespan=lifespan)
    _install_exception_handlers(application)
    _install_routes(application, configured_runtime_mode)
    return application


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


def _install_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(ServiceUnavailableError)
    async def service_unavailable_handler(
        request: Request,
        exc: ServiceUnavailableError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=503,
            content={"code": exc.code, "retryable": True},
        )

    @application.exception_handler(SessionBusyError)
    async def session_busy_handler(
        request: Request,
        exc: SessionBusyError,
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=409,
            content={
                "code": "session_busy",
                "sessionId": exc.session_id,
                "retryable": True,
            },
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error_handler(
        request: Request,
        exc: SQLAlchemyError,
    ) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=503,
            content={"code": "database_unavailable", "retryable": True},
        )


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
            "openhands": get_openhands_capabilities(),
            "readiness": readiness,
        }

    @application.get("/openhands/capabilities")
    def openhands_capabilities() -> dict[str, Any]:
        return get_openhands_capabilities()

    @application.post("/sessions")
    def create_session(
        request: CreateSessionRequest,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
        manager: Annotated[ConversationManager, Depends(get_conversation_manager)],
    ) -> dict[str, str]:
        session_id = f"sess_{uuid4().hex}"
        conversation_id = str(uuid5(NAMESPACE_URL, f"focusproof:{session_id}"))
        now = datetime.now(UTC)
        runtime_mode = configured_runtime_mode
        record = StoredSession(
            session_id=session_id,
            owner_user_id=identity.verified_user_id,
            status="running",
            adapter_mode=runtime_mode,
            domain=request.domain,
            title=request.title,
            goal=request.goal,
            expected_output=request.expectedOutput,
            planned_minutes=request.plannedMinutes,
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
        try:
            manager.get_or_restore(session_id, identity.verified_user_id)
        except (RuntimeUnavailableError, RuntimeCreationError, ValueError):
            pass
        return {"sessionId": session_id, "status": "running"}

    @application.post("/sessions/{session_id}/evidence")
    def submit_evidence(
        session_id: str,
        request: SubmitEvidenceRequest,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
        manager: Annotated[ConversationManager, Depends(get_conversation_manager)],
    ) -> dict[str, str | bool]:
        _owned_session(uow_factory, session_id, identity.verified_user_id)
        evidence_id = f"ev_{uuid4().hex}"
        record = StoredEvidence(
            evidence_id=evidence_id,
            session_id=session_id,
            evidence_type=request.evidenceType,
            content_hash=hash_evidence_content(request.textContent, request.sourceUrl),
            text_content=request.textContent,
            source_url=request.sourceUrl,
            metadata=request.metadata,
            conversation_synced_at=None,
            created_at=datetime.now(UTC),
        )
        with uow_factory() as uow:
            uow.evidence.add(record)
            uow.commit()
        sync_pending = False
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
        request: SubmitAnswerRequest,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
        manager: Annotated[ConversationManager, Depends(get_conversation_manager)],
    ) -> dict[str, str | bool]:
        _owned_session(uow_factory, session_id, identity.verified_user_id)
        with uow_factory() as uow:
            uow.answers.upsert(session_id, request.questionId, request.answer)
            uow.commit()
        sync_pending = False
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
            "questionId": request.questionId,
            "syncPending": sync_pending,
        }

    @application.post("/sessions/{session_id}/review", response_model=None)
    def review_session(
        session_id: str,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
        manager: Annotated[ConversationManager, Depends(get_conversation_manager)],
    ) -> dict[str, Any] | JSONResponse:
        _owned_session(uow_factory, session_id, identity.verified_user_id)
        try:
            result = manager.run_review(session_id, identity.verified_user_id)
        except RuntimeUnavailableError:
            return _runtime_unavailable(session_id, "unavailable")
        except (RuntimeCreationError, ValueError):
            return _runtime_unavailable(session_id, "failed")
        if result.reviewStatus == "failed":
            return JSONResponse(status_code=503, content=result.model_dump(mode="json"))
        with uow_factory() as uow:
            session = uow.sessions.get(session_id)
            latest = uow.audit_events.latest(session_id)
            events_count = len(uow.audit_events.list(session_id))
        response = result.model_dump(mode="json")
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
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    ) -> dict[str, Any]:
        session = _owned_session(uow_factory, session_id, identity.verified_user_id)
        with uow_factory() as uow:
            evidence = uow.evidence.list_for_session(session_id)
            answers = uow.answers.list_for_session(session_id)
        goal = _goal(session)
        runtime_evidence = [_runtime_evidence(item) for item in evidence]
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
            "view": _view(session_id, session.status, goal, runtime_evidence, review),
        }

    @application.get("/sessions/{session_id}/events")
    def get_events(
        session_id: str,
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    ) -> dict[str, list[dict[str, Any]]]:
        _owned_session(uow_factory, session_id, identity.verified_user_id)
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
        identity: Annotated[VerifiedIdentity, Depends(get_verified_identity)],
        uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    ) -> dict[str, list[dict[str, Any]]]:
        _owned_session(uow_factory, session_id, identity.verified_user_id)
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

    @application.get("/debug/openhands/env-status")
    def debug_openhands_env_status() -> dict[str, Any]:
        return get_env_status()

    @application.get("/debug/openhands/llm-status")
    def debug_openhands_llm_status() -> dict[str, Any]:
        return get_llm_config_status()

    @application.post("/debug/openhands/conversation-test")
    def debug_openhands_conversation_test(
        request: DebugConversationTestRequest,
    ) -> dict[str, Any]:
        return real_conversation.run_real_learning_review_spike(
            goal=request.goal,
            evidence=request.evidence,
            domain=request.domain,
        )


def get_uow_factory(request: Request) -> UnitOfWorkFactory:
    _require_ready(request)
    return cast(UnitOfWorkFactory, request.app.state.uow_factory)


def get_conversation_manager(request: Request) -> ConversationManager:
    _require_ready(request)
    return cast(ConversationManager, request.app.state.conversation_manager)


def get_event_log(request: Request) -> PersistentAuditEventLog:
    _require_ready(request)
    return cast(PersistentAuditEventLog, request.app.state.audit_log)


def get_session_repository(request: Request) -> UowEvidenceProvider:
    _require_ready(request)
    return cast(UowEvidenceProvider, request.app.state.evidence_provider)


def _require_ready(request: Request) -> None:
    code = getattr(request.app.state, "readiness_error", "database_unavailable")
    if code is not None:
        raise ServiceUnavailableError(code)


def _database_url_from_environment() -> str:
    return os.environ.get("DATABASE_URL") or (
        "sqlite+pysqlite:///./var/focusproof.db"
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


def _available_tools() -> list[ToolDescription]:
    return [
        ToolDescription(
            name="FocusProofEvidenceVerificationTool",
            description="Verifies repository evidence by ID without assigning a score.",
            inputSchema={"type": "object", "properties": {"evidence_id": {"type": "string"}}},
        ),
        ToolDescription(
            name="FocusProofLearnerInputTool",
            description="Requests focused learner input.",
            inputSchema={"type": "object", "properties": {}},
        ),
        ToolDescription(
            name="FocusProofReviewDraftTool",
            description="Submits findings without a numeric score.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


def _view(
    session_id: str,
    status: str,
    goal: LearningGoal,
    evidence: list[Evidence],
    review: ReviewResult | None,
) -> dict[str, Any]:
    return AgentView(
        session=SessionView(id=session_id, status=status),
        goal=goal,
        evidence=evidence,
        verificationResults=[],
        findings=review.findings if review else [],
        unansweredQuestions=[],
        availableTools=_available_tools(),
        previousActions=[],
    ).model_dump(mode="json")


def _runtime_unavailable(session_id: str, mode: RuntimeMode) -> JSONResponse:
    result = RuntimeReviewResult(
        sessionId=session_id,
        conversationMode=mode,
        usedOpenHandsConversation=False,
        reviewStatus="failed",
        error="OpenHands runtime is unavailable",
    )
    return JSONResponse(status_code=503, content=result.model_dump(mode="json"))


app = create_app()
