from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM

from focusproof.api.app import _database_url_from_environment, create_app


def _migrate(project_root: Path, database_url: str) -> None:
    config = Config(project_root / "alembic.ini")
    config.set_main_option(
        "script_location", str(project_root / "agent-server/migrations")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _review_llm(session_id: str) -> TestLLM:
    del session_id
    call = MessageToolCall(
        id="call_api_restart_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Evidence is specific."],
                "understanding_findings": ["Answer explains replay."],
                "contradictions": [],
                "recommended_next_step": "Add one branch example.",
                "confidence": 0.8,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Submit draft")],
                tool_calls=[call],
            )
        ]
    )


def _awaiting_llm(session_id: str) -> TestLLM:
    del session_id
    call = MessageToolCall(
        id="call_api_restart_question",
        name="focusproof_learner_input",
        arguments=json.dumps(
            {
                "question": "Explain replay in your own words.",
                "reason": "Understanding evidence is missing.",
                "requested_evidence_type": "text",
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Ask learner")],
                tool_calls=[call],
            )
        ]
    )


def _create_session(client: TestClient) -> str:
    response = client.post(
        "/sessions",
        json={
            "domain": "general",
            "title": "Replay",
            "goal": "Explain event replay",
            "owner_user_id": "attacker-controlled",
            "sender": "attacker-controlled",
        },
    )
    assert response.status_code == 200
    return str(response.json()["sessionId"])


def test_fastapi_restart_preserves_session_events_and_reviews(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'api-restart.sqlite3'}"
    data_dir = tmp_path
    _migrate(project_root, database_url)
    app_1 = create_app(
        database_url=database_url,
        data_dir=data_dir,
        llm_factory=_review_llm,
    )
    with TestClient(app_1) as client:
        session_id = _create_session(client)
        evidence = client.post(
            f"/sessions/{session_id}/evidence",
            json={
                "evidenceType": "text",
                "textContent": "Events are appended and replayed into a view.",
            },
        )
        assert evidence.status_code == 200
        review = client.post(f"/sessions/{session_id}/review")
        assert review.status_code == 200
        conversation_id = review.json()["conversationId"]
        events_before = client.get(f"/sessions/{session_id}/events").json()["events"]

    app_2 = create_app(
        database_url=database_url,
        data_dir=data_dir,
        llm_factory=_review_llm,
    )
    with TestClient(app_2) as client:
        state = client.get(f"/sessions/{session_id}")
        events = client.get(f"/sessions/{session_id}/events")
        reviews = client.get(f"/sessions/{session_id}/reviews")

    assert state.status_code == 200
    assert state.json()["state"]["ownerUserId"] == "dev-anonymous-user"
    assert state.json()["state"]["conversationId"] == conversation_id
    assert events.status_code == 200
    assert events.json()["events"] == events_before
    assert reviews.status_code == 200
    assert len(reviews.json()["reviews"]) == 1


def test_review_lock_timeout_returns_top_level_409(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'api-lock.sqlite3'}"
    _migrate(project_root, database_url)
    app = create_app(
        database_url=database_url,
        data_dir=tmp_path,
        lock_timeout_seconds=0.05,
        llm_factory=_review_llm,
    )
    with TestClient(app) as client:
        session_id = _create_session(client)
        with app.state.run_lock.acquire(session_id):
            response = client.post(f"/sessions/{session_id}/review")

    assert response.status_code == 409
    assert response.json() == {
        "code": "session_busy",
        "sessionId": session_id,
        "retryable": True,
    }


def test_restart_can_get_session_while_review_is_awaiting_user(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'awaiting.sqlite3'}"
    data_dir = tmp_path
    _migrate(project_root, database_url)
    app_1 = create_app(
        database_url=database_url,
        data_dir=data_dir,
        llm_factory=_awaiting_llm,
    )
    with TestClient(app_1) as client:
        session_id = _create_session(client)
        review = client.post(f"/sessions/{session_id}/review")
        assert review.json()["reviewStatus"] == "awaiting_user"

    app_2 = create_app(
        database_url=database_url,
        data_dir=data_dir,
        llm_factory=_awaiting_llm,
    )
    with TestClient(app_2) as client:
        response = client.get(f"/sessions/{session_id}")

    assert response.status_code == 200
    assert response.json()["state"]["status"] == "awaiting_user"
    assert response.json()["state"]["reviewResult"] is None


def test_schema_out_of_date_is_sanitized(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'empty.sqlite3'}"
    app = create_app(
        database_url=database_url,
        data_dir=tmp_path,
        llm_factory=_review_llm,
    )
    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            json={"domain": "general", "title": "Replay", "goal": "Explain"},
        )

    assert response.status_code == 503
    assert response.json() == {"code": "schema_out_of_date", "retryable": True}


def test_sqlite_locked_is_sanitized(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[3]
    database_path = tmp_path / "locked.sqlite3"
    migration_url = f"sqlite+pysqlite:///{database_path}"
    _migrate(project_root, migration_url)
    app = create_app(
        database_url=f"{migration_url}?timeout=0.05",
        data_dir=tmp_path,
        llm_factory=_review_llm,
    )
    with TestClient(app) as client:
        blocker = sqlite3.connect(database_path, isolation_level=None)
        try:
            blocker.execute("BEGIN EXCLUSIVE")
            response = client.post(
                "/sessions",
                json={"domain": "general", "title": "Locked", "goal": "Explain"},
            )
        finally:
            blocker.rollback()
            blocker.close()

    assert response.status_code == 503
    assert response.json() == {"code": "database_unavailable", "retryable": True}


def test_legacy_acceptance_session_is_not_imported(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'legacy.sqlite3'}"
    _migrate(project_root, database_url)
    app = create_app(
        database_url=database_url,
        data_dir=tmp_path,
        llm_factory=_review_llm,
    )
    with TestClient(app) as client:
        response = client.get("/sessions/sess_dab3c3e60b78458d9a839ac3d3ff9511")

    assert response.status_code == 404


def test_blank_database_environment_uses_safe_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "")

    assert _database_url_from_environment() == (
        "sqlite+pysqlite:///./var/focusproof.db"
    )


def test_sqlite_database_outside_data_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="FOCUSPROOF_DATA_DIR"):
        create_app(
            database_url=f"sqlite+pysqlite:///{tmp_path / 'outside.sqlite3'}",
            data_dir=tmp_path / "data",
            llm_factory=_review_llm,
        )
