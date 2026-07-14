from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from openhands.sdk.event import MessageEvent
from openhands.sdk.testing import TestLLM
from sqlalchemy import Engine, update

from agent_server_test_support import PersistentEvidenceProvider
from focusproof.openhands_runtime.locks import FileSessionRunLock
from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.openhands_runtime.synchronizer import message_key_from_event
from focusproof.persistence.database import create_database_engine, create_session_factory
from focusproof.persistence.event_log import PersistentAuditEventLog
from focusproof.persistence.models import Base, EvidenceModel
from focusproof.persistence.repositories import StoredEvidence, StoredSession
from focusproof.persistence.unit_of_work import UnitOfWorkFactory


OWNER = "verified-user-1"


def _database(tmp_path: Path, name: str) -> tuple[Engine, UnitOfWorkFactory]:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return engine, UnitOfWorkFactory(create_session_factory(engine))


def _seed(uow_factory: UnitOfWorkFactory, session_id: str) -> None:
    now = datetime.now(UTC)
    with uow_factory() as uow:
        uow.sessions.create(
            StoredSession(
                session_id=session_id,
                owner_user_id=OWNER,
                status="running",
                adapter_mode="openhands-local-scripted-test",
                domain="general",
                title="Restore ordering",
                goal="Keep native order during restore",
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
        )
        uow.evidence.add(
            StoredEvidence(
                evidence_id="ev_old",
                session_id=session_id,
                evidence_type="text",
                content_hash="sha256:old",
                text_content="An old persisted evidence message.",
                source_url=None,
                metadata={},
                conversation_synced_at=None,
                created_at=now,
            )
        )
        uow.commit()


def _manager(
    tmp_path: Path,
    uow_factory: UnitOfWorkFactory,
) -> ConversationManager:
    return ConversationManager(
        repository=PersistentEvidenceProvider(uow_factory),
        audit_log=PersistentAuditEventLog(uow_factory),
        project_root=tmp_path,
        llm_factory=lambda session_id: TestLLM.from_messages([]),
        uow_factory=uow_factory,
        run_lock=FileSessionRunLock(tmp_path / "locks", timeout_seconds=0.5),
    )


def _message_keys(manager: ConversationManager, session_id: str) -> list[str | None]:
    return [
        message_key_from_event(event)
        for event in manager.get(session_id).conversation.state.events
        if isinstance(event, MessageEvent)
    ]


def test_restore_reconciles_old_native_events_before_pending_messages(
    tmp_path: Path,
) -> None:
    session_id = "sess_restore_order"
    engine, uow_factory = _database(tmp_path, "restore-order.sqlite3")
    _seed(uow_factory, session_id)

    first = _manager(tmp_path, uow_factory)
    first.get_or_restore(session_id, OWNER)
    with uow_factory() as uow:
        old_events = uow.audit_events.list(session_id)
    assert [event.type for event in old_events] == [
        "goal.submitted",
        "evidence.submitted",
    ]
    first.close_all()

    with uow_factory() as uow:
        uow.evidence.add(
            StoredEvidence(
                evidence_id="ev_pending",
                session_id=session_id,
                evidence_type="text",
                content_hash="sha256:pending",
                text_content="A pending evidence message added after shutdown.",
                source_url=None,
                metadata={},
                conversation_synced_at=None,
                created_at=datetime.now(UTC),
            )
        )
        uow.answers.upsert(session_id, "q_pending", "A pending learner answer.")
        uow.commit()

    restored = _manager(tmp_path, uow_factory)
    restored.get_or_restore(session_id, OWNER)
    with uow_factory() as uow:
        events_after_restore = uow.audit_events.list(session_id)
        reviews_after_restore = uow.reviews.list_for_session(session_id)

    assert [event.type for event in events_after_restore] == [
        "goal.submitted",
        "evidence.submitted",
        "evidence.submitted",
        "answer.submitted",
    ]
    source_indices = [
        event.payload["sourceOpenHandsEventIndex"] for event in events_after_restore
    ]
    native_events = list(restored.get(session_id).conversation.state.events)
    native_index_by_id = {event.id: index for index, event in enumerate(native_events)}
    assert list(native_index_by_id.values()) == list(range(len(native_events)))
    assert source_indices == sorted(source_indices)
    assert len(source_indices) == len(set(source_indices))
    assert source_indices == [
        native_index_by_id[event.payload["sourceOpenHandsEventId"]]
        for event in events_after_restore
    ]
    assert reviews_after_restore == []
    keys_after_restore = _message_keys(restored, session_id)
    assert keys_after_restore.count("evidence:ev_old") == 1
    assert keys_after_restore.count("evidence:ev_pending") == 1
    assert keys_after_restore.count(
        f"answer:{session_id}:q_pending:1"
    ) == 1
    restored.close_all()

    restored_again = _manager(tmp_path, uow_factory)
    restored_again.get_or_restore(session_id, OWNER)
    with uow_factory() as uow:
        events_after_second_restore = uow.audit_events.list(session_id)
        reviews_after_second_restore = uow.reviews.list_for_session(session_id)

    assert len(events_after_second_restore) == len(events_after_restore)
    assert [event.event_id for event in events_after_second_restore] == [
        event.event_id for event in events_after_restore
    ]
    assert reviews_after_second_restore == reviews_after_restore
    assert _message_keys(restored_again, session_id) == keys_after_restore
    restored_again.close_all()
    engine.dispose()


def test_restore_marks_native_message_without_db_marker_and_does_not_resend(
    tmp_path: Path,
) -> None:
    session_id = "sess_restore_crash_window"
    engine, uow_factory = _database(tmp_path, "restore-crash-window.sqlite3")
    _seed(uow_factory, session_id)

    first = _manager(tmp_path, uow_factory)
    first.get_or_restore(session_id, OWNER)
    original_keys = _message_keys(first, session_id)
    with uow_factory() as uow:
        original_audit_ids = [
            event.event_id for event in uow.audit_events.list(session_id)
        ]
    first.close_all()

    with engine.begin() as connection:
        connection.execute(
            update(EvidenceModel)
            .where(
                EvidenceModel.session_id == session_id,
                EvidenceModel.evidence_id == "ev_old",
            )
            .values(conversation_synced_at=None)
        )

    restored = _manager(tmp_path, uow_factory)
    restored.get_or_restore(session_id, OWNER)
    with uow_factory() as uow:
        evidence = uow.evidence.get(session_id, "ev_old")
        restored_audit_ids = [
            event.event_id for event in uow.audit_events.list(session_id)
        ]

    assert evidence is not None
    assert evidence.conversation_synced_at is not None
    assert _message_keys(restored, session_id) == original_keys
    assert _message_keys(restored, session_id).count("evidence:ev_old") == 1
    assert restored_audit_ids == original_audit_ids
    restored.close_all()
    engine.dispose()
