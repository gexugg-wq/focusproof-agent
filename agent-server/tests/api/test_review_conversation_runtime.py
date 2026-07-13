import json
from pathlib import Path
from collections.abc import Callable

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
import pytest
from fastapi import FastAPI

from focusproof.api import app as app_module


def _review_draft_llm(session_id: str) -> TestLLM:
    del session_id
    draft_call = MessageToolCall(
        id="call_api_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Evidence is specific enough to review."],
                "understanding_findings": ["The learner names append and replay."],
                "contradictions": [],
                "recommended_next_step": "Explain one replay branch example.",
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
                tool_calls=[draft_call],
            )
        ]
    )


def _create_session(client: TestClient) -> str:
    response = client.post(
        "/sessions",
        json={
            "domain": "general",
            "title": "Learn event replay",
            "goal": "Explain append-only event replay",
        },
    )
    assert response.status_code == 200
    return str(response.json()["sessionId"])


def _migrated_app(
    tmp_path: Path,
    *,
    llm_factory: Callable[[str], TestLLM] | None = None,
) -> FastAPI:
    project_root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'review-runtime.sqlite3'}"
    config = Config(project_root / "alembic.ini")
    config.set_main_option("script_location", str(project_root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return app_module.create_app(
        database_url=database_url,
        data_dir=tmp_path,
        llm_factory=llm_factory,
    )


def test_review_uses_injected_sdk_local_conversation_without_public_mode_flag(
    tmp_path: Path,
) -> None:
    test_app = _migrated_app(tmp_path, llm_factory=_review_draft_llm)
    with TestClient(test_app) as client:
        session_id = _create_session(client)
        evidence_response = client.post(
            f"/sessions/{session_id}/evidence",
            json={
                "evidenceType": "text",
                "textContent": "Events are appended and replayed into a current view.",
            },
        )
        assert evidence_response.status_code == 200

        response = client.post(f"/sessions/{session_id}/review")

        assert response.status_code == 200
        data = response.json()
        assert data["conversationMode"] == "openhands-local-scripted-test"
        assert data["usedOpenHandsConversation"] is True
        assert data["conversationId"]
        assert data["nativeEventCount"] >= 5
        assert data["actionEventsCount"] >= 1
        assert data["observationEventsCount"] >= 1
        assert data["reviewStatus"] == "completed"
        assert data["reviewResult"] is not None
        assert data["error"] is None

        events = client.get(f"/sessions/{session_id}/events").json()["events"]
        review_event = next(event for event in events if event["type"] == "review.completed")
        assert review_event["payload"]["sourceOpenHandsEventType"] == "ObservationEvent"
        assert review_event["payload"]["sourceOpenHandsEventId"]


def test_review_returns_structured_503_when_llm_config_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "focusproof.openhands_runtime.factory.build_openhands_llm_config",
        lambda project_root: None,
    )
    test_app = _migrated_app(tmp_path)
    with TestClient(test_app) as client:
        session_id = _create_session(client)
        response = client.post(f"/sessions/{session_id}/review")

        assert response.status_code == 503
        data = response.json()
        assert data["conversationMode"] == "unavailable"
        assert data["usedOpenHandsConversation"] is False
        assert data["reviewStatus"] == "failed"
        assert data["reviewResult"] is None
        assert data["error"]


def test_formal_review_openapi_has_no_public_runtime_mode_request() -> None:
    operation = app_module.app.openapi()["paths"]["/sessions/{session_id}/review"][
        "post"
    ]

    assert "requestBody" not in operation
