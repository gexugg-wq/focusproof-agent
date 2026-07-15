from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM


def _completed_review_llm(_: str) -> TestLLM:
    draft = MessageToolCall(
        id="call_ai4b_idempotent_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Repository-backed evidence is available."],
                "understanding_findings": ["The explanation includes replay details."],
                "contradictions": [],
                "recommended_next_step": "Add one more worked example.",
                "confidence": 0.7,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Submit review draft")],
                tool_calls=[draft],
            )
        ]
    )


def _create_session(client: TestClient) -> str:
    response = client.post(
        "/sessions",
        json={
            "domain": "programming",
            "title": "Understand replay",
            "goal": "Explain how event replay rebuilds application state.",
            "expectedOutput": "A worked explanation",
            "plannedMinutes": 30,
        },
    )
    assert response.status_code == 200
    return str(response.json()["sessionId"])


def _event_count(client: TestClient, session_id: str, event_type: str) -> int:
    events = client.get(f"/sessions/{session_id}/events").json()["events"]
    return sum(event["type"] == event_type for event in events)


def test_duplicate_evidence_is_one_persisted_and_synchronized_record(
    ai4b_app_factory: Callable[..., Any],
) -> None:
    with ai4b_app_factory(_completed_review_llm) as running:
        session_id = _create_session(running.client)
        first_payload = {
            "evidenceType": "text",
            "textContent": (
                "Replay starts from an empty state and applies each retained event in order."
            ),
            "metadata": {"source": "notes", "attempt": 1},
        }
        second_payload = {
            "metadata": {"attempt": 1, "source": "notes"},
            "textContent": first_payload["textContent"],
            "evidenceType": "text",
        }

        first = running.client.post(
            f"/sessions/{session_id}/evidence", json=first_payload
        )
        second = running.client.post(
            f"/sessions/{session_id}/evidence", json=second_payload
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["evidenceId"] == first.json()["evidenceId"]
        state = running.client.get(f"/sessions/{session_id}").json()["state"]
        assert len(state["evidence"]) == 1
        assert _event_count(running.client, session_id, "evidence.submitted") == 1


def test_concurrent_duplicate_evidence_has_one_winner(
    ai4b_app_factory: Callable[..., Any],
) -> None:
    with ai4b_app_factory(_completed_review_llm) as running:
        session_id = _create_session(running.client)
        payload = {
            "evidenceType": "text",
            "textContent": "A reducer applies immutable events to rebuild one current view.",
            "metadata": {"source": "concurrent-test"},
        }

        def submit() -> Any:
            return running.client.post(
                f"/sessions/{session_id}/evidence",
                json=payload,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: submit(), range(2)))

        assert [response.status_code for response in responses] == [200, 200]
        assert len({response.json()["evidenceId"] for response in responses}) == 1
        state = running.client.get(f"/sessions/{session_id}").json()["state"]
        assert len(state["evidence"]) == 1
        assert _event_count(running.client, session_id, "evidence.submitted") == 1


def test_distinct_metadata_remains_distinct_evidence(
    ai4b_app_factory: Callable[..., Any],
) -> None:
    with ai4b_app_factory(_completed_review_llm) as running:
        session_id = _create_session(running.client)
        payload = {
            "evidenceType": "text",
            "textContent": "Replay applies events in sequence to rebuild a view.",
        }
        first = running.client.post(
            f"/sessions/{session_id}/evidence",
            json={**payload, "metadata": {"attempt": 1}},
        )
        second = running.client.post(
            f"/sessions/{session_id}/evidence",
            json={**payload, "metadata": {"attempt": 2}},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["evidenceId"] != second.json()["evidenceId"]
        state = running.client.get(f"/sessions/{session_id}").json()["state"]
        assert len(state["evidence"]) == 2


def test_duplicate_answer_does_not_append_a_second_message(
    ai4b_app_factory: Callable[..., Any],
) -> None:
    with ai4b_app_factory(_completed_review_llm) as running:
        session_id = _create_session(running.client)
        payload = {
            "questionId": "q_replay",
            "answer": "Replay applies each event in order from an empty state.",
        }
        first = running.client.post(f"/sessions/{session_id}/answer", json=payload)
        second = running.client.post(f"/sessions/{session_id}/answer", json=payload)

        assert first.status_code == 200
        assert second.status_code == 200
        state = running.client.get(f"/sessions/{session_id}").json()["state"]
        assert state["answers"] == {"q_replay": payload["answer"]}
        assert _event_count(running.client, session_id, "answer.submitted") == 1


def test_completed_review_replay_returns_existing_result_without_new_events(
    ai4b_app_factory: Callable[..., Any],
) -> None:
    with ai4b_app_factory(_completed_review_llm) as running:
        session_id = _create_session(running.client)
        evidence = running.client.post(
            f"/sessions/{session_id}/evidence",
            json={
                "evidenceType": "text",
                "textContent": (
                    "Event replay starts from an empty state and applies each event in order."
                ),
            },
        )
        assert evidence.status_code == 200

        first = running.client.post(f"/sessions/{session_id}/review")
        events_after_first = running.client.get(
            f"/sessions/{session_id}/events"
        ).json()["events"]
        second = running.client.post(f"/sessions/{session_id}/review")
        events_after_second = running.client.get(
            f"/sessions/{session_id}/events"
        ).json()["events"]
        reviews = running.client.get(f"/sessions/{session_id}/reviews").json()[
            "reviews"
        ]

        assert first.status_code == 200
        assert first.json()["reviewStatus"] == "completed"
        assert second.status_code == 200
        assert second.json()["reviewStatus"] == "completed"
        assert second.json()["reviewResult"] == first.json()["reviewResult"]
        assert events_after_second == events_after_first
        assert len(reviews) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("domain", " "),
        ("domain", "d" * 129),
        ("title", " "),
        ("title", "t" * 513),
        ("goal", " "),
        ("goal", "g" * 20_001),
        ("expectedOutput", "o" * 20_001),
        ("plannedMinutes", 0),
        ("plannedMinutes", 525_601),
    ],
)
def test_session_input_bounds_return_422(
    field: str,
    value: object,
    ai4b_app_factory: Callable[..., Any],
) -> None:
    payload: dict[str, object] = {
        "domain": "general",
        "title": "Bounded session",
        "goal": "Explain bounded input handling.",
        "expectedOutput": "A short explanation",
        "plannedMinutes": 20,
    }
    payload[field] = value
    with ai4b_app_factory(_completed_review_llm) as running:
        response = running.client.post("/sessions", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"evidenceType": " ", "textContent": "Specific notes"},
        {"evidenceType": "e" * 129, "textContent": "Specific notes"},
        {"evidenceType": "text", "textContent": " "},
        {"evidenceType": "text", "textContent": "x" * 100_001},
        {
            "evidenceType": "url",
            "sourceUrl": "https://example.com",
            "textContent": " ",
        },
        {"evidenceType": "url", "textContent": "A source explanation"},
        {
            "evidenceType": "url",
            "sourceUrl": "https://example.com/" + "u" * 2_100,
            "textContent": "A source explanation",
        },
        {
            "evidenceType": "text",
            "textContent": "Specific notes",
            "metadata": {f"key-{index}": index for index in range(101)},
        },
        {
            "evidenceType": "text",
            "textContent": "Specific notes",
            "metadata": {"level1": {"level2": {"level3": {"level4": {"level5": {"level6": "too deep"}}}}}},
        },
        {
            "evidenceType": "text",
            "textContent": "Specific notes",
            "metadata": {"large": "m" * 16_385},
        },
    ],
)
def test_evidence_input_bounds_and_shape_return_422(
    payload: dict[str, object],
    ai4b_app_factory: Callable[..., Any],
) -> None:
    with ai4b_app_factory(_completed_review_llm) as running:
        session_id = _create_session(running.client)
        response = running.client.post(
            f"/sessions/{session_id}/evidence",
            json=payload,
        )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"questionId": " ", "answer": "Specific answer"},
        {"questionId": "q" * 129, "answer": "Specific answer"},
        {"questionId": "q_1", "answer": " "},
        {"questionId": "q_1", "answer": "a" * 20_001},
    ],
)
def test_answer_input_bounds_return_422(
    payload: dict[str, str],
    ai4b_app_factory: Callable[..., Any],
) -> None:
    with ai4b_app_factory(_completed_review_llm) as running:
        session_id = _create_session(running.client)
        response = running.client.post(
            f"/sessions/{session_id}/answer",
            json=payload,
        )

    assert response.status_code == 422


def test_oversized_request_body_is_rejected_before_validation(
    ai4b_app_factory: Callable[..., Any],
) -> None:
    sentinel = "ai4b-body-secret-sentinel"
    body = json.dumps(
        {
            "domain": "general",
            "title": "Oversized",
            "goal": sentinel + ("x" * 300_000),
        }
    )
    with ai4b_app_factory(_completed_review_llm) as running:
        response = running.client.post(
            "/sessions",
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json() == {"code": "request_too_large", "retryable": False}
    assert sentinel not in response.text


def test_chunked_oversized_request_is_rejected_without_content_length(
    ai4b_app_factory: Callable[..., Any],
) -> None:
    def chunks() -> Any:
        yield b'{"domain":"general","title":"Chunked","goal":"'
        yield b"x" * 300_000
        yield b'"}'

    with ai4b_app_factory(_completed_review_llm) as running:
        response = running.client.post(
            "/sessions",
            content=chunks(),
            headers={
                "content-type": "application/json",
                "transfer-encoding": "chunked",
            },
        )

    assert response.status_code == 413
    assert response.json() == {"code": "request_too_large", "retryable": False}
