from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.llm.exceptions import LLMRateLimitError
from openhands.sdk.testing import TestLLM
import pytest

from focusproof.api.app import create_app


def _recovering_llm(session_id: str) -> TestLLM:
    del session_id
    draft_call = MessageToolCall(
        id="call_recovered_review_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Evidence is specific and repository-backed."],
                "understanding_findings": ["The explanation describes replay."],
                "contradictions": [],
                "recommended_next_step": "Add one concrete replay example.",
                "confidence": 0.8,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            LLMRateLimitError("provider quota exhausted"),
            Message(
                role="assistant",
                content=[TextContent(text="Submit recovered review")],
                tool_calls=[draft_call],
            ),
        ]
    )


def _migrated_app(tmp_path: Path) -> FastAPI:
    root = Path(__file__).resolve().parents[3]
    url = f"sqlite+pysqlite:///{tmp_path / 'provider-recovery.sqlite3'}"
    config = Config(root / "alembic.ini")
    config.set_main_option("script_location", str(root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_app(
        database_url=url,
        data_dir=tmp_path,
        llm_factory=_recovering_llm,
    )


def test_provider_infrastructure_failure_is_retryable_without_learning_pollution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "local-dev")
    monkeypatch.setenv("FOCUSPROOF_DATA_DIR", str(tmp_path))
    app = _migrated_app(tmp_path)

    with TestClient(app) as client:
        created = client.post(
            "/sessions",
            json={
                "domain": "general",
                "title": "Provider recovery",
                "goal": "Explain durable replay",
            },
        )
        session_id = created.json()["sessionId"]
        submitted = client.post(
            f"/sessions/{session_id}/evidence",
            json={
                "evidenceType": "text",
                "textContent": "Durable events can rebuild the current state.",
            },
        )
        assert submitted.status_code == 200

        manager = app.state.conversation_manager
        before_attempt = list(manager.get(session_id).conversation.state.events)
        first = client.post(f"/sessions/{session_id}/review")
        after_attempt = list(manager.get(session_id).conversation.state.events)
        first_state = client.get(f"/sessions/{session_id}").json()["state"]
        first_reviews = client.get(f"/sessions/{session_id}/reviews").json()["reviews"]
        first_events = client.get(f"/sessions/{session_id}/events").json()["events"]

        assert first.status_code == 503
        assert first.json() == {
            "code": "runtime_unavailable",
            "retryable": True,
        }
        assert len(after_attempt) > len(before_attempt)
        assert first_state["status"] == "running"
        assert first_state["reviewResult"] is None
        assert first_reviews == []
        first_event_types = [event["type"] for event in first_events]
        assert "score.calculated" not in first_event_types
        assert "review.completed" not in first_event_types
        assert "review.failed" not in first_event_types
        assert not any(
            event["type"].startswith("observation.learning_failure")
            for event in first_events
        )

        second = client.post(f"/sessions/{session_id}/review")
        final_state = client.get(f"/sessions/{session_id}").json()["state"]
        final_reviews = client.get(f"/sessions/{session_id}/reviews").json()["reviews"]
        final_events = client.get(f"/sessions/{session_id}/events").json()["events"]

    assert second.status_code == 200
    assert second.json()["reviewStatus"] == "completed"
    assert final_state["status"] == "reviewed"
    assert final_state["reviewResult"] is not None
    assert len(final_reviews) == 1
    assert final_reviews[0]["reviewStatus"] == "completed"
    final_event_types = [event["type"] for event in final_events]
    assert final_event_types.count("score.calculated") == 1
    assert final_event_types.count("review.completed") == 1


@pytest.mark.parametrize(
    "exception_type",
    [
        "LLMRateLimitError",
        "LLMTimeoutError",
        "LLMServiceUnavailableError",
    ],
)
def test_official_provider_infrastructure_types_are_runtime_unavailable(
    exception_type: str,
) -> None:
    from focusproof.openhands_runtime import manager as manager_module

    exception_class = getattr(
        __import__(
            "openhands.sdk.llm.exceptions",
            fromlist=[exception_type],
        ),
        exception_type,
    )
    wrapped = RuntimeError("SDK conversation wrapper")
    wrapped.__cause__ = exception_class("provider unavailable")

    assert manager_module._is_provider_infrastructure_failure(wrapped)
