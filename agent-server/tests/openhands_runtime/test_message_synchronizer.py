from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from openhands.sdk.event import MessageEvent

from focusproof.openhands_runtime.factory import ConversationFactory
from focusproof.openhands_runtime.handle import ConversationHandle
from focusproof.openhands_runtime.synchronizer import (
    ConversationSynchronizer,
    message_key_from_event,
    serialize_message_envelope,
)
from focusproof.persistence.database import create_database_engine, create_session_factory
from focusproof.persistence.models import Base
from focusproof.persistence.repositories import StoredEvidence, StoredSession
from focusproof.persistence.unit_of_work import UnitOfWorkFactory
from focusproof.runtime.evidence import Evidence, LearningGoal

from .conftest import completed_review_llm


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


def _services(tmp_path: Path) -> tuple[UnitOfWorkFactory, ConversationFactory]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'sync.sqlite3'}"
    )
    Base.metadata.create_all(engine)
    uow_factory = UnitOfWorkFactory(create_session_factory(engine))
    factory = ConversationFactory(
        project_root=tmp_path,
        repository=PersistentEvidenceProvider(uow_factory),
        llm_factory=completed_review_llm,
    )
    return uow_factory, factory


def _seed(uow_factory: UnitOfWorkFactory, session_id: str) -> None:
    now = datetime.now(UTC)
    with uow_factory() as uow:
        uow.sessions.create(
            StoredSession(
                session_id=session_id,
                owner_user_id="verified-user-1",
                status="running",
                adapter_mode="openhands-local-scripted-test",
                domain="general",
                title="Replay",
                goal="Explain replay",
                expected_output=None,
                planned_minutes=20,
                conversation_id=str(
                    uuid5(NAMESPACE_URL, f"focusproof:{session_id}")
                ),
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
                evidence_id="ev_1",
                session_id=session_id,
                evidence_type="text",
                content_hash="sha256:test",
                text_content="Specific replay notes",
                source_url=None,
                metadata={},
                conversation_synced_at=None,
                created_at=now,
            )
        )
        uow.answers.upsert(session_id, "q_1", "Replay rebuilds the view")
        uow.commit()


def _messages(handle: ConversationHandle) -> list[MessageEvent]:
    return [
        event
        for event in handle.conversation.state.events
        if isinstance(event, MessageEvent) and event.source == "user"
    ]


def test_sync_sends_stable_keys_with_verified_sender_once(
    tmp_path: Path,
    learning_goal: LearningGoal,
) -> None:
    session_id = "sess_sync"
    uow_factory, factory = _services(tmp_path)
    _seed(uow_factory, session_id)
    handle = factory.create(session_id, learning_goal)
    synchronizer = ConversationSynchronizer(uow_factory)
    try:
        first = synchronizer.sync(handle, verified_user_id="verified-user-1")
        second = synchronizer.sync(handle, verified_user_id="verified-user-1")
        messages = _messages(handle)
    finally:
        handle.conversation.close()

    assert first.sent_count == 3
    assert second.sent_count == 0
    assert {message_key_from_event(event) for event in messages} == {
        f"goal:{session_id}",
        "evidence:ev_1",
        f"answer:{session_id}:q_1:1",
    }
    assert {event.sender for event in messages} == {"verified-user-1"}
    with uow_factory() as uow:
        session = uow.sessions.get(session_id)
        evidence = uow.evidence.get(session_id, "ev_1")
        answers = uow.answers.list_for_session(session_id)
    assert session is not None and session.goal_conversation_synced_at is not None
    assert evidence is not None and evidence.conversation_synced_at is not None
    assert answers[0].conversation_synced_at is not None


def test_native_message_without_db_marker_is_marked_without_resend(
    tmp_path: Path,
    learning_goal: LearningGoal,
) -> None:
    session_id = "sess_crash_window"
    uow_factory, factory = _services(tmp_path)
    _seed(uow_factory, session_id)
    handle = factory.create(session_id, learning_goal)
    envelope = serialize_message_envelope(
        schema_version=1,
        message_key="evidence:ev_1",
        kind="evidence",
        session_id=session_id,
        payload={"evidence_id": "ev_1"},
    )
    cast(Any, handle.conversation).send_message(
        envelope,
        sender="verified-user-1",
    )
    synchronizer = ConversationSynchronizer(uow_factory)
    try:
        result = synchronizer.sync(handle, verified_user_id="verified-user-1")
        keys = [message_key_from_event(event) for event in _messages(handle)]
    finally:
        handle.conversation.close()

    assert result.sent_count == 2
    assert keys.count("evidence:ev_1") == 1
    with uow_factory() as uow:
        evidence = uow.evidence.get(session_id, "ev_1")
    assert evidence is not None and evidence.conversation_synced_at is not None


def test_message_key_parser_rejects_malformed_json() -> None:
    from openhands.sdk.event import MessageEvent
    from openhands.sdk.llm import Message, TextContent

    event = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="not json")]),
    )
    assert message_key_from_event(event) is None
