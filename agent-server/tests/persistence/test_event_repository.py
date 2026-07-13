from __future__ import annotations

from datetime import UTC, datetime

from focusproof.persistence.repositories import StoredReview
from focusproof.persistence.unit_of_work import UnitOfWorkFactory

from .test_session_repository import _session


def test_audit_native_source_is_idempotent(
    uow_factory: UnitOfWorkFactory,
) -> None:
    with uow_factory() as uow:
        uow.sessions.create(_session())
        first = uow.audit_events.append(
            "sess_1",
            "verification.completed",
            "tool",
            {"verified": True},
            source_openhands_event_id="native_1",
        )
        second = uow.audit_events.append(
            "sess_1",
            "verification.completed",
            "tool",
            {"verified": True},
            source_openhands_event_id="native_1",
        )
        uow.commit()

    with uow_factory() as uow:
        events = uow.audit_events.list("sess_1")
    assert second.event_id == first.event_id
    assert len(events) == 1
    assert events[0].sequence == 1


def test_review_native_source_is_idempotent_and_history_is_preserved(
    uow_factory: UnitOfWorkFactory,
) -> None:
    now = datetime.now(UTC)
    first_record = StoredReview(
        review_id="rev_1",
        session_id="sess_1",
        conversation_id="11111111-1111-1111-1111-111111111111",
        review_status="awaiting_user",
        score=None,
        result=None,
        native_event_count=3,
        source_openhands_event_id="native_question_1",
        created_at=now,
    )
    duplicate_record = first_record.model_copy(update={"review_id": "rev_duplicate"})
    completed_record = StoredReview(
        review_id="rev_2",
        session_id="sess_1",
        conversation_id=first_record.conversation_id,
        review_status="completed",
        score=77,
        result={"score": 77},
        native_event_count=8,
        source_openhands_event_id="native_draft_1",
        created_at=now,
    )
    with uow_factory() as uow:
        uow.sessions.create(_session())
        first = uow.reviews.add_from_native_event(first_record)
        duplicate = uow.reviews.add_from_native_event(duplicate_record)
        uow.reviews.add_from_native_event(completed_record)
        uow.commit()

    with uow_factory() as uow:
        reviews = uow.reviews.list_for_session("sess_1")
    assert duplicate.review_id == first.review_id
    assert [review.review_id for review in reviews] == ["rev_1", "rev_2"]


def test_audit_latest_and_has_source_event(
    uow_factory: UnitOfWorkFactory,
) -> None:
    with uow_factory() as uow:
        uow.sessions.create(_session())
        uow.audit_events.append(
            "sess_1", "goal.submitted", "user", {}, source_openhands_event_id="n1"
        )
        latest = uow.audit_events.append(
            "sess_1", "question.asked", "agent", {}, source_openhands_event_id="n2"
        )
        uow.commit()
    with uow_factory() as uow:
        assert uow.audit_events.has_source_event("sess_1", "n2") is True
        stored_latest = uow.audit_events.latest("sess_1")
    assert stored_latest is not None
    assert stored_latest.event_id == latest.event_id
    assert stored_latest.sequence == latest.sequence
