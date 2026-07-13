from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from focusproof.persistence.repositories import (
    StoredAnswer,
    StoredEvidence,
    StoredSession,
)
from focusproof.persistence.unit_of_work import UnitOfWorkFactory


def _session(session_id: str = "sess_1") -> StoredSession:
    now = datetime.now(UTC)
    return StoredSession(
        session_id=session_id,
        owner_user_id="dev-anonymous-user",
        status="running",
        adapter_mode="openhands-local-scripted-test",
        domain="general",
        title="Replay",
        goal="Explain replay",
        expected_output=None,
        planned_minutes=20,
        conversation_id=str(uuid5(NAMESPACE_URL, f"focusproof:{session_id}")),
        runtime_mode="openhands-local-scripted-test",
        review_result=None,
        goal_conversation_synced_at=None,
        version=1,
        created_at=now,
        updated_at=now,
    )


def test_unit_of_work_rolls_back_without_commit(
    uow_factory: UnitOfWorkFactory,
) -> None:
    with uow_factory() as uow:
        uow.sessions.create(_session())

    with uow_factory() as uow:
        assert uow.sessions.get("sess_1") is None


def test_session_and_evidence_commit_and_sync_markers(
    uow_factory: UnitOfWorkFactory,
) -> None:
    evidence = StoredEvidence(
        evidence_id="ev_1",
        session_id="sess_1",
        evidence_type="text",
        content_hash="sha256:test",
        text_content="Specific notes",
        source_url=None,
        metadata={"source": "notes"},
        conversation_synced_at=None,
        created_at=datetime.now(UTC),
    )
    with uow_factory() as uow:
        uow.sessions.create(_session())
        uow.evidence.add(evidence)
        uow.commit()

    synced_at = datetime.now(UTC)
    with uow_factory() as uow:
        uow.sessions.mark_goal_synced("sess_1", synced_at)
        uow.evidence.mark_synced("sess_1", "ev_1", synced_at)
        uow.commit()

    with uow_factory() as uow:
        stored = uow.sessions.get("sess_1")
        stored_evidence = uow.evidence.get("sess_1", "ev_1")
    assert stored is not None
    assert stored.goal_conversation_synced_at is not None
    assert stored_evidence is not None
    assert stored_evidence.metadata == {"source": "notes"}
    assert stored_evidence.conversation_synced_at is not None


def test_answer_upsert_increments_version_and_resets_sync(
    uow_factory: UnitOfWorkFactory,
) -> None:
    with uow_factory() as uow:
        uow.sessions.create(_session())
        first = uow.answers.upsert("sess_1", "q_1", "First answer")
        uow.answers.mark_synced("sess_1", "q_1", first.version, datetime.now(UTC))
        uow.commit()

    with uow_factory() as uow:
        second = uow.answers.upsert("sess_1", "q_1", "Improved answer")
        uow.commit()

    assert isinstance(second, StoredAnswer)
    assert second.version == 2
    assert second.conversation_synced_at is None


def test_session_status_uses_optimistic_version(
    uow_factory: UnitOfWorkFactory,
) -> None:
    with uow_factory() as uow:
        uow.sessions.create(_session())
        uow.commit()

    with uow_factory() as uow:
        updated = uow.sessions.update_status("sess_1", "awaiting_user", expected_version=1)
        stale = uow.sessions.update_status("sess_1", "failed", expected_version=1)
        uow.commit()

    assert updated is True
    assert stale is False
