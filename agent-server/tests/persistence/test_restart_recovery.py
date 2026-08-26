from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.event import MessageEvent
from sqlalchemy import Engine
from openhands.sdk.testing import TestLLM

from focusproof.openhands_runtime.locks import FileSessionRunLock
from focusproof.openhands_runtime.factory import LLMFactory
from focusproof.openhands_runtime.factory import RuntimeCreationError
from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.openhands_runtime.synchronizer import message_key_from_event
from focusproof.persistence.database import create_database_engine, create_session_factory
from focusproof.persistence.audit_projection import PersistentAuditProjectionStore
from focusproof.persistence.models import Base
from focusproof.persistence.repositories import (
    SqlReviewRepository,
    StoredEvidence,
    StoredSession,
)
from focusproof.persistence.unit_of_work import UnitOfWorkFactory

from agent_server_test_support import PersistentEvidenceProvider


def _awaiting_llm(session_id: str) -> TestLLM:
    del session_id
    call = MessageToolCall(
        id="call_question_restart",
        name="focusproof_learner_input",
        arguments=json.dumps(
            {
                "question": "How does replay rebuild the view?",
                "reason": "Need an explanation.",
                "requested_evidence_type": "text",
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Ask")], tool_calls=[call])]
    )


def _completed_llm(session_id: str) -> TestLLM:
    del session_id
    verify = MessageToolCall(
        id="call_verify_restart",
        name="focusproof_text_evidence_verification",
        arguments=json.dumps({"evidence_id": "ev_1"}),
        origin="completion",
    )
    draft = MessageToolCall(
        id="call_draft_restart",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Repository evidence verified."],
                "understanding_findings": ["Answer explains replay."],
                "contradictions": [],
                "recommended_next_step": "Add a branch example.",
                "confidence": 0.8,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Verify")],
                tool_calls=[verify],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Draft")],
                tool_calls=[draft],
            ),
        ]
    )


def _uow_factory(database_url: str) -> tuple[Engine, UnitOfWorkFactory]:
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    return engine, UnitOfWorkFactory(create_session_factory(engine))


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
                goal="Explain append-only replay",
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
                evidence_id="ev_1",
                session_id=session_id,
                evidence_type="text",
                content_hash="sha256:restart",
                text_content="Append-only events replay into a current view.",
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
    llm_factory: LLMFactory,
) -> ConversationManager:
    return ConversationManager(
        repository=PersistentEvidenceProvider(uow_factory),
        audit_log=PersistentAuditProjectionStore(uow_factory),
        project_root=tmp_path,
        llm_factory=llm_factory,
        uow_factory=uow_factory,
        run_lock=FileSessionRunLock(tmp_path / "var", timeout_seconds=0.2),
    )


def test_restart_restores_native_history_without_duplicate_product_rows(
    tmp_path: Path,
) -> None:
    session_id = "sess_restart"
    database_url = f"sqlite+pysqlite:///{tmp_path / 'restart.sqlite3'}"
    engine_1, uow_1 = _uow_factory(database_url)
    _seed(uow_1, session_id)
    manager_1 = _manager(tmp_path, uow_1, _awaiting_llm)

    first_handle = manager_1.get_or_restore(session_id, "verified-user-1")
    first_result = manager_1.run_review(session_id, "verified-user-1")
    first_native_count = len(first_handle.conversation.state.events)
    first_conversation_id = first_result.conversationId
    with uow_1() as uow:
        first_audit_count = len(uow.audit_events.list(session_id))
        first_reviews = uow.reviews.list_for_session(session_id)
    assert first_conversation_id is not None
    assert len(first_conversation_id) == 32
    assert "-" not in first_conversation_id
    assert first_result.reviewStatus == "awaiting_user"
    assert len(first_reviews) == 1

    manager_1.close_all()
    engine_1.dispose()

    engine_2, uow_2 = _uow_factory(database_url)
    manager_2 = _manager(tmp_path, uow_2, _completed_llm)
    restored = manager_2.get_or_restore(session_id, "verified-user-1")
    restored_keys = [
        message_key_from_event(event)
        for event in restored.conversation.state.events
        if isinstance(event, MessageEvent)
    ]
    assert restored.conversation_id.hex == first_conversation_id
    assert len(restored.conversation.state.events) == first_native_count
    assert restored_keys.count(f"goal:{session_id}") == 1
    assert restored_keys.count("evidence:ev_1") == 1
    with uow_2() as uow:
        assert len(uow.audit_events.list(session_id)) == first_audit_count
        uow.answers.upsert(
            session_id,
            first_result.agentQuestions[0]["questionId"],
            "Replay rebuilds the derived view from immutable facts.",
        )
        uow.commit()

    manager_2.send_answer(session_id, "verified-user-1")
    completed = manager_2.run_review(session_id, "verified-user-1")
    assert completed.reviewStatus == "completed"
    assert completed.conversationId == first_conversation_id
    retried = manager_2.run_review(session_id, "verified-user-1")
    assert retried.reviewStatus == "completed"
    with uow_2() as uow:
        reviews = uow.reviews.list_for_session(session_id)
        source_ids = [review.source_openhands_event_id for review in reviews]
        final_event_types = [event.type for event in uow.audit_events.list(session_id)]
    assert [review.review_status for review in reviews] == ["awaiting_user", "completed"]
    assert len(source_ids) == len(set(source_ids))
    assert final_event_types.count("score.calculated") == 1
    assert final_event_types.count("review.completed") == 1

    manager_2.close_all()
    engine_2.dispose()


def test_review_persistence_failure_rolls_back_final_audit_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "sess_review_failure"
    database_url = f"sqlite+pysqlite:///{tmp_path / 'review-failure.sqlite3'}"
    engine, uow_factory = _uow_factory(database_url)
    _seed(uow_factory, session_id)
    manager = _manager(tmp_path, uow_factory, _completed_llm)

    def fail_review_persistence(self: SqlReviewRepository, record: object) -> None:
        del self, record
        raise RuntimeError("review persistence failed")

    monkeypatch.setattr(
        SqlReviewRepository,
        "add_from_native_event",
        fail_review_persistence,
    )

    with pytest.raises(RuntimeError, match="review persistence failed"):
        manager.run_review(session_id, "verified-user-1")

    with uow_factory() as uow:
        event_types = [event.type for event in uow.audit_events.list(session_id)]
    assert "score.calculated" not in event_types
    assert "review.completed" not in event_types
    manager.close_all()
    engine.dispose()


def test_corrupt_openhands_persistence_does_not_create_a_new_conversation(
    tmp_path: Path,
) -> None:
    session_id = "sess_corrupt"
    database_url = f"sqlite+pysqlite:///{tmp_path / 'corrupt.sqlite3'}"
    engine, uow_factory = _uow_factory(database_url)
    _seed(uow_factory, session_id)
    conversation_id = uuid5(NAMESPACE_URL, f"focusproof:{session_id}")
    persistence_root = (
        tmp_path / "var" / "conversations" / session_id / "persistence" / conversation_id.hex
    )
    persistence_root.mkdir(parents=True)
    (persistence_root / "base_state.json").write_text("{corrupt", encoding="utf-8")
    manager = _manager(tmp_path, uow_factory, _awaiting_llm)

    with pytest.raises(RuntimeCreationError):
        manager.get_or_restore(session_id, "verified-user-1")

    conversation_dirs = list(
        (tmp_path / "var/conversations" / session_id / "persistence").iterdir()
    )
    assert conversation_dirs == [persistence_root]
    manager.close_all()
    engine.dispose()
