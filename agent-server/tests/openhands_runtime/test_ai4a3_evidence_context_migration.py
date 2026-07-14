from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import TextContent
from openhands.sdk.testing import TestLLM

from agent_server_test_support import PersistentEvidenceProvider
from focusproof.openhands_runtime.factory import ConversationFactory
from focusproof.openhands_runtime.projector import OpenHandsEventProjector
from focusproof.openhands_runtime.synchronizer import (
    ConversationSynchronizer,
    message_key_from_event,
    serialize_message_envelope,
)
from focusproof.openhands_runtime.tool_registry import release_repository_provider
from focusproof.persistence.database import create_database_engine, create_session_factory
from focusproof.persistence.models import Base
from focusproof.persistence.repositories import StoredEvidence, StoredSession
from focusproof.persistence.unit_of_work import UnitOfWorkFactory
from focusproof.runtime.evidence import LearningGoal
from focusproof.runtime.event_log import InMemoryEventLog


OWNER = "verified-user-1"


def _event_text(event: MessageEvent) -> str:
    return "".join(
        item.text for item in event.llm_message.content if isinstance(item, TextContent)
    )


def test_old_bodyless_evidence_gets_one_append_only_context_upgrade(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'migration.db'}")
    Base.metadata.create_all(engine)
    uow_factory = UnitOfWorkFactory(create_session_factory(engine))
    session_id = "sess_context_upgrade"
    now = datetime.now(UTC)
    with uow_factory() as uow:
        uow.sessions.create(
            StoredSession(
                session_id=session_id,
                owner_user_id=OWNER,
                status="running",
                adapter_mode="openhands-local-scripted-test",
                domain="general",
                title="Migration",
                goal="Explain append-only migration",
                expected_output=None,
                planned_minutes=10,
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
                text_content="Append-only events preserve the original historical fact.",
                source_url=None,
                metadata={},
                conversation_synced_at=None,
                created_at=now,
            )
        )
        uow.commit()

    conversation_id = uuid5(NAMESPACE_URL, f"focusproof:{session_id}")
    goal = LearningGoal(domain="general", title="M", goal="G")
    audit_log = InMemoryEventLog()
    first_handle = None
    restored_handle = None
    try:
        first_factory = ConversationFactory(
            repository=PersistentEvidenceProvider(uow_factory),
            project_root=tmp_path,
            data_dir=tmp_path / "var",
            llm_factory=lambda current_session_id: TestLLM.from_messages([]),
        )
        first_handle = first_factory.create(
            session_id,
            goal,
            conversation_id=conversation_id,
            user_id=OWNER,
        )
        old_message = serialize_message_envelope(
            schema_version=1,
            message_key="evidence:ev_old",
            kind="evidence",
            session_id=session_id,
            payload={
                "evidenceId": "ev_old",
                "evidenceType": "text",
                "contentHash": "sha256:old",
            },
        )
        cast(Any, first_handle.conversation).send_message(old_message, sender=OWNER)
        original = next(
            event
            for event in first_handle.conversation.state.events
            if isinstance(event, MessageEvent)
            and message_key_from_event(event) == "evidence:ev_old"
        )
        original_json = original.model_dump_json()

        ConversationSynchronizer(uow_factory).sync(
            first_handle,
            verified_user_id=OWNER,
        )
        first_events = list(first_handle.conversation.state.events)
        first_context = [
            event
            for event in first_events
            if isinstance(event, MessageEvent)
            and message_key_from_event(event) == "evidence-context:ev_old:v1"
        ]
        assert len(first_context) == 1
        OpenHandsEventProjector(
            session_id,
            conversation_id,
            audit_log,
        ).reconcile(first_events)
        assert len(audit_log.get_by_type(session_id, "evidence.submitted")) == 1

        first_handle.conversation.close()
        first_handle = None
        release_repository_provider()

        restored_factory = ConversationFactory(
            repository=PersistentEvidenceProvider(uow_factory),
            project_root=tmp_path,
            data_dir=tmp_path / "var",
            llm_factory=lambda current_session_id: TestLLM.from_messages([]),
        )
        restored_handle = restored_factory.create(
            session_id,
            goal,
            conversation_id=conversation_id,
            user_id=OWNER,
        )
        assert restored_handle.compatibility_restore is True
        ConversationSynchronizer(uow_factory).sync(
            restored_handle,
            verified_user_id=OWNER,
        )
        restored_events = list(restored_handle.conversation.state.events)
        restored_original = next(
            event
            for event in restored_events
            if isinstance(event, MessageEvent)
            and message_key_from_event(event) == "evidence:ev_old"
        )
        restored_context = [
            event
            for event in restored_events
            if isinstance(event, MessageEvent)
            and message_key_from_event(event) == "evidence-context:ev_old:v1"
        ]

        assert restored_original.model_dump_json() == original_json
        assert len(restored_context) == 1
        envelope = json.loads(_event_text(restored_context[0]))
        assert envelope["kind"] == "evidence_context"
        assert "Append-only events preserve" in envelope["payload"]["textContent"]
        assert envelope["payload"]["contextSchemaVersion"] == 1
        assert envelope["payload"]["contentTrust"] == "untrusted"

        OpenHandsEventProjector(
            session_id,
            conversation_id,
            audit_log,
        ).reconcile(restored_events)
        assert len(audit_log.get_by_type(session_id, "evidence.submitted")) == 1
    finally:
        if first_handle is not None:
            first_handle.conversation.close()
        if restored_handle is not None:
            restored_handle.conversation.close()
        release_repository_provider()
        engine.dispose()
