import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM

from focusproof.api import app as app_module

app = app_module.app


def _review_llm(session_id: str) -> TestLLM:
    del session_id
    call = MessageToolCall(
        id="call_api_sessions_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Specific evidence was submitted."],
                "understanding_findings": ["The answer explains immutable replay."],
                "contradictions": [],
                "recommended_next_step": "Add one branch replay example.",
                "confidence": 0.8,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Submit review draft")],
                tool_calls=[call],
            )
        ]
    )


def test_health_and_openhands_capabilities() -> None:
    client = TestClient(app)

    health = client.get("/health")
    capabilities = client.get("/openhands/capabilities")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert "openhands" in health.json()
    assert capabilities.status_code == 200
    assert "adapterMode" in capabilities.json()


def test_session_evidence_review_and_events_flow(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'api-sessions.sqlite3'}"
    config = Config(project_root / "alembic.ini")
    config.set_main_option("script_location", str(project_root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    test_app = app_module.create_app(
        database_url=database_url,
        data_dir=tmp_path,
        llm_factory=_review_llm,
    )
    with TestClient(test_app) as client:
        session_response = client.post(
            "/sessions",
            json={
                "domain": "general",
                "title": "Learn event logs",
                "goal": "Explain event replay",
                "expectedOutput": "summary",
                "plannedMinutes": 25,
            },
        )
        assert session_response.status_code == 200
        session_id = session_response.json()["sessionId"]
        evidence_response = client.post(
            f"/sessions/{session_id}/evidence",
            json={
                "evidenceType": "text",
                "textContent": "Events are appended to a log and replayed into a current view.",
                "metadata": {"source": "notes"},
            },
        )
        assert evidence_response.status_code == 200
        assert evidence_response.json()["sessionId"] == session_id

        answer_response = client.post(
            f"/sessions/{session_id}/answer",
            json={
                "questionId": "q_manual",
                "answer": "Replay keeps the facts immutable while views change.",
            },
        )
        assert answer_response.status_code == 200

        review_response = client.post(f"/sessions/{session_id}/review")
        assert review_response.status_code == 200
        review_json = review_response.json()
        assert review_json["eventsCount"] >= 4
        assert review_json["reviewResult"]["score"] >= 60

        state_response = client.get(f"/sessions/{session_id}")
        assert state_response.status_code == 200
        assert state_response.json()["sessionId"] == session_id
        assert state_response.json()["view"]["goal"]["title"] == "Learn event logs"

        events_response = client.get(f"/sessions/{session_id}/events")
        assert events_response.status_code == 200
        assert len(events_response.json()["events"]) == review_json["eventsCount"]
