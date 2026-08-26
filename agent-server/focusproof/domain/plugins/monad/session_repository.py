from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, ConfigDict, PrivateAttr, model_serializer

from focusproof.domain.plugins.monad.repository import MonadClaimRepository
from focusproof.domain.plugins.monad.verifier import MonadEvidenceVerifier
from focusproof.openhands_runtime.tools import SessionEvidenceRepository
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike
from focusproof.runtime.evidence import Evidence


class BoundMonadSessionRepository(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    _repository: SessionEvidenceRepository = PrivateAttr()
    _verifier: MonadEvidenceVerifier = PrivateAttr()
    _principal_id: str = PrivateAttr()
    _uow_factory: UnitOfWorkFactoryLike = PrivateAttr()

    @classmethod
    def bind(
        cls,
        repository: SessionEvidenceRepository,
        *,
        verifier: MonadEvidenceVerifier,
        principal_id: str,
        uow_factory: UnitOfWorkFactoryLike,
    ) -> "BoundMonadSessionRepository":
        bound = cls()
        bound._repository = repository
        bound._verifier = verifier
        bound._principal_id = principal_id
        bound._uow_factory = uow_factory
        return bound

    @model_serializer
    def _serialize_without_runtime_binding(self) -> dict[str, Any]:
        return {}

    def get_monad_verifier(self) -> MonadEvidenceVerifier:
        return self._verifier

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        stored = self._repository.get_evidence(session_id, evidence_id)
        if stored.evidenceType != "monad_transaction":
            return stored
        metadata = dict(stored.metadata)
        metadata["explanation"] = stored.textContent or ""
        metadata["sessionStartedAt"] = self._session_started_at(session_id)
        return Evidence(
            evidenceId=stored.evidenceId,
            evidenceType=stored.evidenceType,
            contentHash=stored.contentHash,
            textContent=stored.textContent,
            sourceUrl=stored.sourceUrl,
            metadata=metadata,
        )

    def claim_monad_transaction(self, **values: Any) -> None:
        with self._uow_factory() as uow:
            session = cast(Any, uow)._require_session()
            MonadClaimRepository(session).claim(
                chain_id=int(values["chain_id"]),
                tx_hash=str(values["transaction_hash"]),
                session_id=str(values["session_id"]),
                evidence_id=str(values["evidence_id"]),
                observation_event_id=str(values["observation_event_id"]),
            )
            uow.commit()

    def _session_started_at(self, session_id: str) -> str:
        with self._uow_factory() as uow:
            session = uow.sessions.get_owned(session_id, self._principal_id)
        if session is None:
            raise KeyError(f"Session {session_id} does not exist")
        return session.created_at.isoformat()
