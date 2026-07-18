from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from pathlib import Path
from threading import Condition, RLock
from typing import Any, ContextManager, Protocol, cast
from uuid import UUID, uuid4

from openhands.sdk.conversation.types import ConversationCallbackType
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.base import Event as OpenHandsEvent

from focusproof.domain.review import ReviewResult
from focusproof.config.profiles import RuntimeSettings
from focusproof.openhands_runtime.evidence_messages import runtime_evidence_payload
from focusproof.openhands_runtime.factory import (
    ConversationFactory,
    LLMFactory,
    RuntimeUnavailableError,
)
from focusproof.openhands_runtime.handle import ConversationHandle, RuntimeReviewResult
from focusproof.openhands_runtime.locks import SessionRunLock
from focusproof.openhands_runtime.projector import OpenHandsEventProjector
from focusproof.openhands_runtime.provider_admission import (
    ProviderAdmission,
    ProviderAdmissionUnavailableError,
)
from focusproof.openhands_runtime.result_extractor import RuntimeResultExtractor
from focusproof.openhands_runtime.synchronizer import ConversationSynchronizer
from focusproof.openhands_runtime.tools import SessionEvidenceRepository
from focusproof.openhands_runtime.tools.learner_input import LearnerInputObservation
from focusproof.openhands_runtime.tools.review_draft import ReviewDraftObservation
from focusproof.persistence.repositories import StoredSession
from focusproof.persistence.providers import UowEvidenceProvider
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.audit_projection import AuditProjectionStore


DEFAULT_REVIEW_TIMEOUT_SECONDS = 60.0


class _NoopSessionRunLock:
    def acquire(self, session_id: str) -> ContextManager[None]:
        del session_id
        from contextlib import nullcontext

        return nullcontext()


class _AsyncRunnableConversation(Protocol):
    def arun(self) -> Awaitable[None]: ...


class ConversationManager:
    def __init__(
        self,
        *,
        repository: SessionEvidenceRepository,
        audit_log: AuditProjectionStore,
        project_root: Path | None = None,
        data_dir: Path | None = None,
        llm_factory: LLMFactory | None = None,
        review_timeout_seconds: float = DEFAULT_REVIEW_TIMEOUT_SECONDS,
        uow_factory: UnitOfWorkFactoryLike | None = None,
        run_lock: SessionRunLock | None = None,
        provider_admission: ProviderAdmission | None = None,
        runtime_settings: RuntimeSettings | None = None,
    ) -> None:
        self._audit_log = audit_log
        self._lifecycle_lock = RLock()
        self._lifecycle_changed = Condition(self._lifecycle_lock)
        self._active_reviews: dict[str, set[str]] = {}
        self._running_reviews: dict[str, str] = {}
        self._interrupted_reviews: set[tuple[str, str]] = set()
        self._shutdown_complete = False
        self._shutdown_error: BaseException | None = None
        self._handles: dict[str, ConversationHandle] = {}
        self._projectors: dict[str, OpenHandsEventProjector] = {}
        self._evidence_ids: dict[str, set[str]] = {}
        self._evidence: dict[str, dict[str, Evidence]] = {}
        self._answers: dict[str, dict[str, str]] = {}
        self._goals: dict[str, LearningGoal] = {}
        self._uow_factory = uow_factory
        self._run_lock = run_lock or _NoopSessionRunLock()
        self._provider_admission = provider_admission
        self._synchronizer = (
            ConversationSynchronizer(uow_factory) if uow_factory is not None else None
        )
        self._result_extractor = RuntimeResultExtractor(audit_log, uow_factory)
        self._review_timeout_seconds = review_timeout_seconds
        factory_repository = (
            UowEvidenceProvider(uow_factory)
            if uow_factory is not None
            else repository
        )
        self._factory = ConversationFactory(
            repository=factory_repository,
            project_root=project_root,
            data_dir=data_dir,
            llm_factory=llm_factory,
            callback_factory=self._create_projector_callback,
            runtime_settings=runtime_settings,
            compatibility_mode=uow_factory is None,
        )
        self._accepting_reviews = True

    def create(
        self,
        session_id: str,
        goal: LearningGoal,
        verified_user_id: str | None = None,
    ) -> ConversationHandle:
        if self._uow_factory is not None:
            if verified_user_id is None:
                raise ValueError("verified_user_id is required")
            return self.get_or_restore(session_id, verified_user_id)
        with self._run_lock.acquire(session_id):
            return self._create_legacy_unlocked(session_id, goal)

    def get(self, session_id: str) -> ConversationHandle:
        try:
            return self._handles[session_id]
        except KeyError as exc:
            raise KeyError(f"No OpenHands conversation for session {session_id}") from exc

    def get_or_restore(
        self,
        session_id: str,
        verified_user_id: str,
    ) -> ConversationHandle:
        if self._uow_factory is None:
            return self.get(session_id)
        with self._run_lock.acquire(session_id):
            return self._get_or_restore_unlocked(session_id, verified_user_id)

    def send_evidence(
        self,
        session_id: str,
        evidence_or_user_id: Evidence | str,
    ) -> None:
        if isinstance(evidence_or_user_id, Evidence):
            with self._run_lock.acquire(session_id):
                self._send_legacy_evidence_unlocked(session_id, evidence_or_user_id)
            return
        self._sync_persistent_session(session_id, evidence_or_user_id)

    def send_answer(
        self,
        session_id: str,
        question_or_user_id: str,
        answer: str | None = None,
    ) -> None:
        if answer is not None:
            with self._run_lock.acquire(session_id):
                self._send_legacy_answer_unlocked(session_id, question_or_user_id, answer)
            return
        self._sync_persistent_session(session_id, question_or_user_id)

    def run_review(
        self,
        session_id: str,
        verified_user_id: str | None = None,
        review_call_id: str | None = None,
    ) -> RuntimeReviewResult:
        call_id = review_call_id or uuid4().hex
        with self._lifecycle_lock:
            if not self._accepting_reviews:
                raise RuntimeUnavailableError(
                    "Conversation manager is shutting down"
                )
            self._active_reviews.setdefault(session_id, set()).add(call_id)
        try:
            return self._run_review(session_id, verified_user_id, call_id)
        finally:
            with self._lifecycle_changed:
                active_calls = self._active_reviews[session_id]
                active_calls.discard(call_id)
                if not active_calls:
                    self._active_reviews.pop(session_id)
                if self._running_reviews.get(session_id) == call_id:
                    self._running_reviews.pop(session_id)
                self._interrupted_reviews.discard((session_id, call_id))
                self._lifecycle_changed.notify_all()

    def _run_review(
        self,
        session_id: str,
        verified_user_id: str | None,
        review_call_id: str,
    ) -> RuntimeReviewResult:
        with self._run_lock.acquire(session_id):
            with self._lifecycle_lock:
                if not self._accepting_reviews:
                    raise RuntimeUnavailableError(
                        "Conversation manager is shutting down"
                    )
                self._running_reviews[session_id] = review_call_id
            if self._uow_factory is not None:
                if verified_user_id is None:
                    raise ValueError("verified_user_id is required")
                handle = self._get_or_restore_unlocked(session_id, verified_user_id)
                session = self._load_session(session_id)
                if session.status == "reviewed" and session.review_result is not None:
                    native_events = list(handle.conversation.state.events)
                    return RuntimeReviewResult(
                        sessionId=session_id,
                        conversationMode=handle.runtime_mode,
                        usedOpenHandsConversation=True,
                        conversationId=str(handle.conversation_id),
                        nativeEventCount=len(native_events),
                        messageEventsCount=sum(
                            isinstance(event, MessageEvent) for event in native_events
                        ),
                        actionEventsCount=sum(
                            isinstance(event, ActionEvent) for event in native_events
                        ),
                        observationEventsCount=sum(
                            isinstance(event, ObservationEvent) for event in native_events
                        ),
                        projectedEventsCount=len(self._audit_log.list(session_id)),
                        reviewStatus="completed",
                        reviewResult=ReviewResult.model_validate(session.review_result),
                    )
                goal, evidence, answers = self._load_scoring_facts(session_id)
            else:
                handle = self.get(session_id)
                goal = self._goals[session_id]
                evidence = list(self._evidence[session_id].values())
                answers = list(self._answers[session_id].values())
            with self._lifecycle_lock:
                closing = not self._accepting_reviews
                interrupted = (
                    session_id, review_call_id
                ) in self._interrupted_reviews
            if closing or interrupted:
                handle.conversation.interrupt()
                if closing:
                    raise RuntimeUnavailableError(
                        "Conversation manager is shutting down"
                    )
                return self._failure_result(handle, "CancelledError")
            native_events = list(handle.conversation.state.events)
            recovered = self._result_extractor.extract(
                handle=handle,
                native_events=native_events,
                goal=goal,
                evidence=evidence,
                answers=answers,
            )
            if recovered.reviewStatus != "failed":
                return recovered
            try:
                if self._provider_admission is None:
                    asyncio.run(
                        asyncio.wait_for(
                            cast(_AsyncRunnableConversation, handle.conversation).arun(),
                            timeout=self._review_timeout_seconds,
                        )
                    )
                else:
                    with self._provider_admission.acquire():
                        asyncio.run(
                            asyncio.wait_for(
                                cast(
                                    _AsyncRunnableConversation, handle.conversation
                                ).arun(),
                                timeout=self._review_timeout_seconds,
                            )
                        )
            except ProviderAdmissionUnavailableError:
                raise
            except TimeoutError:
                handle.conversation.interrupt()
                return self._failure_result(handle, "TimeoutError")
            except Exception as exc:
                return self._failure_result(handle, type(exc).__name__)

            native_events = list(handle.conversation.state.events)
            projector = self._projectors[session_id]
            projector.reconcile(native_events)
            handle.projected_event_ids = {
                str(event.payload["sourceOpenHandsEventId"])
                for event in self._audit_log.list(session_id)
                if "sourceOpenHandsEventId" in event.payload
            }
            return self._result_extractor.extract(
                handle=handle,
                native_events=native_events,
                goal=goal,
                evidence=evidence,
                answers=answers,
            )

    def close(
        self,
        session_id: str,
        verified_user_id: str | None = None,
    ) -> None:
        with self._run_lock.acquire(session_id):
            if self._uow_factory is not None:
                if verified_user_id is None:
                    raise ValueError("verified_user_id is required")
                self._assert_owner(session_id, verified_user_id)
            self._close_unlocked(session_id)

    def interrupt(self, session_id: str, review_call_id: str | None = None) -> None:
        with self._lifecycle_lock:
            active_calls = self._active_reviews.get(session_id, set())
            if review_call_id is None:
                self._interrupted_reviews.update(
                    (session_id, call_id) for call_id in active_calls
                )
                should_interrupt = session_id in self._running_reviews
            else:
                if review_call_id in active_calls:
                    self._interrupted_reviews.add((session_id, review_call_id))
                should_interrupt = (
                    self._running_reviews.get(session_id) == review_call_id
                )
            handle = self._handles.get(session_id) if should_interrupt else None
        if handle is not None:
            handle.conversation.interrupt()

    def close_all(self) -> None:
        with self._lifecycle_changed:
            if not self._accepting_reviews:
                while not self._shutdown_complete and self._shutdown_error is None:
                    self._lifecycle_changed.wait()
                if self._shutdown_error is not None:
                    raise RuntimeError("Conversation manager shutdown failed") from (
                        self._shutdown_error
                    )
                return
            self._accepting_reviews = False
            self._interrupted_reviews.update(
                (session_id, call_id)
                for session_id, call_ids in self._active_reviews.items()
                for call_id in call_ids
            )
            active_handles = [
                handle
                for session_id, handle in self._handles.items()
                if session_id in self._running_reviews
            ]
            for handle in active_handles:
                handle.conversation.interrupt()
        try:
            with self._lifecycle_changed:
                while self._active_reviews:
                    self._lifecycle_changed.wait()
                session_ids = list(self._handles)
            for session_id in session_ids:
                with self._run_lock.acquire(session_id):
                    self._close_unlocked(session_id)
        except BaseException as exc:
            with self._lifecycle_changed:
                self._shutdown_error = exc
                self._lifecycle_changed.notify_all()
            raise
        with self._lifecycle_changed:
            self._shutdown_complete = True
            self._lifecycle_changed.notify_all()

    def _get_or_restore_unlocked(
        self,
        session_id: str,
        verified_user_id: str,
    ) -> ConversationHandle:
        existing = self._handles.get(session_id)
        if existing is not None:
            self._assert_owner(session_id, verified_user_id)
            assert self._synchronizer is not None
            self._synchronizer.sync(existing, verified_user_id=verified_user_id)
            return existing

        session = self._load_session(session_id)
        if session.owner_user_id != verified_user_id:
            raise PermissionError("Verified identity does not own this session")
        goal = _learning_goal(session)
        handle = self._factory.create(
            session_id,
            goal,
            conversation_id=UUID(session.conversation_id),
            principal_id=verified_user_id,
            user_id=verified_user_id,
        )
        self._handles[session_id] = handle
        try:
            native_events_at_restore = list(handle.conversation.state.events)
            self._projectors[session_id].reconcile(native_events_at_restore)
            assert self._synchronizer is not None
            self._synchronizer.sync(handle, verified_user_id=verified_user_id)
        except Exception:
            self._close_unlocked(session_id)
            raise
        return handle

    def _sync_persistent_session(
        self,
        session_id: str,
        verified_user_id: str,
    ) -> None:
        if self._uow_factory is None:
            raise RuntimeError("Persistent synchronization is not configured")
        with self._run_lock.acquire(session_id):
            handle = self._get_or_restore_unlocked(session_id, verified_user_id)
            assert self._synchronizer is not None
            self._synchronizer.sync(handle, verified_user_id=verified_user_id)

    def _load_session(self, session_id: str) -> StoredSession:
        assert self._uow_factory is not None
        with self._uow_factory() as uow:
            session = uow.sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} does not exist")
        return session

    def _assert_owner(self, session_id: str, verified_user_id: str) -> None:
        if self._load_session(session_id).owner_user_id != verified_user_id:
            raise PermissionError("Verified identity does not own this session")

    def _load_scoring_facts(
        self,
        session_id: str,
    ) -> tuple[LearningGoal, list[Evidence], list[str]]:
        assert self._uow_factory is not None
        with self._uow_factory() as uow:
            session = uow.sessions.get(session_id)
            if session is None:
                raise KeyError(f"Session {session_id} does not exist")
            stored_evidence = uow.evidence.list_for_session(session_id)
            stored_answers = uow.answers.list_for_session(session_id)
        evidence = [
            Evidence(
                evidenceId=item.evidence_id,
                evidenceType=item.evidence_type,
                contentHash=item.content_hash,
                textContent=item.text_content,
                sourceUrl=item.source_url,
                metadata=item.metadata,
            )
            for item in stored_evidence
        ]
        return _learning_goal(session), evidence, [item.answer for item in stored_answers]

    def _create_legacy_unlocked(
        self,
        session_id: str,
        goal: LearningGoal,
    ) -> ConversationHandle:
        existing = self._handles.get(session_id)
        if existing is not None:
            return existing
        handle = self._factory.create(session_id, goal)
        self._handles[session_id] = handle
        self._evidence_ids[session_id] = set()
        self._evidence[session_id] = {}
        self._answers[session_id] = {}
        self._goals[session_id] = goal
        _send_message(
            handle.conversation,
            json.dumps(
                {
                    "kind": "goal",
                    "session_id": session_id,
                    "goal": goal.model_dump(mode="json"),
                },
                sort_keys=True,
            ),
        )
        return handle

    def _send_legacy_evidence_unlocked(
        self,
        session_id: str,
        evidence: Evidence,
    ) -> None:
        handle = self.get(session_id)
        known = self._evidence_ids.setdefault(session_id, set())
        if evidence.evidenceId in known:
            return
        known.add(evidence.evidenceId)
        self._evidence.setdefault(session_id, {})[evidence.evidenceId] = evidence
        _send_message(
            handle.conversation,
            json.dumps(
                {
                    "kind": "evidence",
                    "session_id": session_id,
                    "evidence": runtime_evidence_payload(
                        evidence.model_dump(mode="json")
                    ),
                },
                sort_keys=True,
            ),
        )

    def _send_legacy_answer_unlocked(
        self,
        session_id: str,
        question_id: str,
        answer: str,
    ) -> None:
        handle = self.get(session_id)
        answers = self._answers.setdefault(session_id, {})
        if answers.get(question_id) == answer:
            return
        answers[question_id] = answer
        _send_message(
            handle.conversation,
            json.dumps(
                {
                    "kind": "answer",
                    "session_id": session_id,
                    "question_id": question_id,
                    "answer": answer,
                },
                sort_keys=True,
            ),
        )

    def _close_unlocked(self, session_id: str) -> None:
        handle = self._handles.pop(session_id, None)
        if handle is not None:
            handle.conversation.close()
        self._projectors.pop(session_id, None)
        self._evidence_ids.pop(session_id, None)
        self._evidence.pop(session_id, None)
        self._answers.pop(session_id, None)
        self._goals.pop(session_id, None)

    def _create_projector_callback(
        self,
        session_id: str,
        conversation_id: UUID,
    ) -> ConversationCallbackType:
        projector = OpenHandsEventProjector(session_id, conversation_id, self._audit_log)
        self._projectors[session_id] = projector

        def callback(event: OpenHandsEvent) -> None:
            projector.on_event(event)
            if not isinstance(event, ObservationEvent):
                return
            if not isinstance(
                event.observation,
                (LearnerInputObservation, ReviewDraftObservation),
            ):
                return
            handle = self._handles.get(session_id)
            if handle is not None:
                handle.conversation.pause()

        return callback

    @staticmethod
    def _failure_result(
        handle: ConversationHandle,
        exception_name: str,
    ) -> RuntimeReviewResult:
        native_events = list(handle.conversation.state.events)
        return RuntimeReviewResult(
            sessionId=handle.session_id,
            conversationMode="failed",
            usedOpenHandsConversation=False,
            conversationId=str(handle.conversation_id),
            nativeEventCount=len(native_events),
            messageEventsCount=sum(isinstance(event, MessageEvent) for event in native_events),
            actionEventsCount=sum(isinstance(event, ActionEvent) for event in native_events),
            observationEventsCount=sum(
                isinstance(event, ObservationEvent) for event in native_events
            ),
            projectedEventsCount=0,
            reviewStatus="failed",
            error=f"{exception_name}: OpenHands conversation run failed",
        )


def _learning_goal(session: StoredSession) -> LearningGoal:
    return LearningGoal(
        domain=session.domain,
        title=session.title,
        goal=session.goal,
        expectedOutput=session.expected_output,
        plannedMinutes=session.planned_minutes,
    )


def _send_message(conversation: object, message: str) -> None:
    # The installed SDK's exported LocalConversation type resolves this argument
    # to Never under strict Mypy, while the runtime signature accepts str | Message.
    cast(Any, conversation).send_message(message)
