from focusproof.runtime.event_log import InMemoryEventLog
from focusproof.runtime.events import Event


def test_event_log_append_many_sequences_and_query_are_isolated() -> None:
    log = InMemoryEventLog()
    first = Event(sessionId="sess_1", type="goal.submitted", sequence=99, actor="user", payload={"x": 1})
    second = Event(sessionId="sess_1", type="evidence.submitted", sequence=99, actor="user", payload={"y": 2})

    stored = log.append_many([first, second])
    stored[0].payload["x"] = "mutated"
    listed = log.list("sess_1")
    listed[0].payload["x"] = "also-mutated"

    fresh = log.list("sess_1")
    assert [event.sequence for event in fresh] == [1, 2]
    assert fresh[0].payload["x"] == 1
    assert log.count("sess_1") == 2
    latest = log.latest("sess_1")
    assert latest is not None
    assert latest.type == "evidence.submitted"
    assert [event.type for event in log.get_by_type("sess_1", "goal.submitted")] == ["goal.submitted"]


def test_event_log_finds_projected_native_source_id() -> None:
    log = InMemoryEventLog()
    log.append(
        "sess_1",
        "verification.requested",
        "agent",
        {"sourceOpenHandsEventId": "native_1"},
    )

    assert log.has_source_event("sess_1", "native_1") is True
    assert log.has_source_event("sess_1", "native_2") is False
