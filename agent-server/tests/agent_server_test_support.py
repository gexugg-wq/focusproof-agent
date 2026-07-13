from __future__ import annotations

from focusproof.persistence.unit_of_work import UnitOfWorkFactory
from focusproof.runtime.evidence import Evidence


class PersistentEvidenceProvider:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        with self._uow_factory() as uow:
            stored = uow.evidence.get(session_id, evidence_id)
        if stored is None:
            raise KeyError(evidence_id)
        return Evidence(
            evidenceId=stored.evidence_id,
            evidenceType=stored.evidence_type,
            contentHash=stored.content_hash,
            textContent=stored.text_content,
            sourceUrl=stored.source_url,
            metadata=stored.metadata,
        )
