from __future__ import annotations

from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, Protocol, Self

from sqlalchemy.orm import Session, sessionmaker

from focusproof.persistence.repositories import (
    AnswerRepository,
    AuditEventRepository,
    EvidenceRepository,
    ReviewRepository,
    PrincipalRepository,
    ResourceSlotRepository,
    SecurityAuditRepository,
    SessionRepository,
    SpeechRequestRepository,
    SqlAnswerRepository,
    SqlAuditEventRepository,
    SqlEvidenceRepository,
    SqlReviewRepository,
    SqlPrincipalRepository,
    SqlSecurityAuditRepository,
    SqlSessionRepository,
)


class MediaTransactionPort(Protocol):
    def reserve(self, request: Any) -> Any: ...
    def find_idempotent_outcome(
        self,
        owner_id: str,
        session_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> Any | None: ...
    def finalize(self, request: Any) -> Any: ...
    def confirm_reference(self, intent: Any) -> Any: ...
    def list_pending_reference_outcomes(self, limit: int) -> tuple[Any, ...]: ...
    def reject(self, lease: Any, reason: str) -> None: ...


class UnitOfWork(Protocol):
    sessions: SessionRepository
    evidence: EvidenceRepository
    answers: AnswerRepository
    audit_events: AuditEventRepository
    reviews: ReviewRepository
    principals: PrincipalRepository
    security_audit: SecurityAuditRepository
    media: MediaTransactionPort
    speech_requests: SpeechRequestRepository
    resource_slots: ResourceSlotRepository

    @property
    def scan_audit(self) -> Any: ...

    def __enter__(self) -> Self: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class SqlAlchemyUnitOfWork:
    sessions: SessionRepository
    evidence: EvidenceRepository
    answers: AnswerRepository
    audit_events: AuditEventRepository
    reviews: ReviewRepository
    principals: PrincipalRepository
    security_audit: SecurityAuditRepository
    media: MediaTransactionPort
    speech_requests: SpeechRequestRepository
    resource_slots: ResourceSlotRepository

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        media_max_items: int = 4,
        media_max_distinct_bytes: int = 20 * 1024 * 1024,
        speech_active_hmac_key_version: str | None = None,
        speech_hmac_keys: Mapping[str, bytes] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._committed = False
        self._media_max_items = media_max_items
        self._media_max_distinct_bytes = media_max_distinct_bytes
        self._speech_active_hmac_key_version = speech_active_hmac_key_version
        self._speech_hmac_keys = dict(speech_hmac_keys or {})

    def __enter__(self) -> Self:
        from focusproof.persistence.repositories import (
            _begin_immediate,
            SqlMediaTransactionRepository,
            SqlResourceSlotRepository,
            SqlSpeechRequestRepository,
        )

        self._session = self._session_factory()
        if self._speech_active_hmac_key_version is not None:
            _begin_immediate(self._session)
        self.sessions = SqlSessionRepository(self._session)
        self.evidence = SqlEvidenceRepository(self._session)
        self.answers = SqlAnswerRepository(self._session)
        self.audit_events = SqlAuditEventRepository(self._session)
        self.reviews = SqlReviewRepository(self._session)
        self.principals = SqlPrincipalRepository(self._session)
        self.security_audit = SqlSecurityAuditRepository(self._session)
        self.media = SqlMediaTransactionRepository(
            self._session,
            max_items=self._media_max_items,
            max_distinct_bytes=self._media_max_distinct_bytes,
        )
        self.resource_slots = SqlResourceSlotRepository(self._session)
        self.speech_requests = SqlSpeechRequestRepository(
            self._session,
            active_hmac_key_version=self._speech_active_hmac_key_version,
            hmac_keys=self._speech_hmac_keys,
        )
        return self

    @property
    def scan_audit(self) -> Any:
        from focusproof.persistence.audit_projection import MediaScanAuditRepository

        return MediaScanAuditRepository(self._require_session())

    def commit(self) -> None:
        self._require_session().commit()
        self._committed = True

    def rollback(self) -> None:
        self._require_session().rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc, traceback
        session = self._require_session()
        try:
            if exc_type is not None or not self._committed:
                session.rollback()
        finally:
            session.close()
            self._session = None

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("UnitOfWork must be entered before use")
        return self._session


class UnitOfWorkFactory:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        media_max_items: int = 4,
        media_max_distinct_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self._session_factory = session_factory
        self._media_max_items = media_max_items
        self._media_max_distinct_bytes = media_max_distinct_bytes
        self._speech_active_hmac_key_version: str | None = None
        self._speech_hmac_keys: dict[str, bytes] = {}

    def configure_speech(
        self,
        *,
        active_hmac_key_version: str,
        hmac_keys: Mapping[str, bytes],
    ) -> None:
        if active_hmac_key_version not in hmac_keys:
            raise ValueError("active speech HMAC key is unavailable")
        self._speech_active_hmac_key_version = active_hmac_key_version
        self._speech_hmac_keys = dict(hmac_keys)

    def __call__(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(
            self._session_factory,
            media_max_items=self._media_max_items,
            media_max_distinct_bytes=self._media_max_distinct_bytes,
            speech_active_hmac_key_version=self._speech_active_hmac_key_version,
            speech_hmac_keys=self._speech_hmac_keys,
        )


UnitOfWorkFactoryLike = Callable[[], SqlAlchemyUnitOfWork]
