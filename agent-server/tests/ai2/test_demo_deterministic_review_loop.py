from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openhands.sdk.event import MessageEvent
import pytest

from focusproof.api.app import create_app
from focusproof.openhands_runtime.synchronizer import message_key_from_event

_PROVIDER_ENV_KEYS = (
    "FOCUSPROOF_LLM_PROVIDER",
    "FOCUSPROOF_LLM_MODEL",
    "FOCUSPROOF_LLM_SUPPORTS_VISION",
    "FOCUSPROOF_LLM_BASE_URL",
    "FOCUSPROOF_LLM_API_KEY",
    "FOCUSPROOF_LLM_REQUEST_TIMEOUT_SECONDS",
    "FOCUSPROOF_LLM_NUM_RETRIES",
    "FOCUSPROOF_LLM_RETRY_MIN_WAIT_SECONDS",
    "FOCUSPROOF_LLM_RETRY_MAX_WAIT_SECONDS",
    "FOCUSPROOF_LLM_CONTEXT_WINDOW_TOKENS",
    "FOCUSPROOF_LLM_MAX_OUTPUT_TOKENS",
    "FOCUSPROOF_LLM_MAX_ITERATIONS",
    "FOCUSPROOF_LLM_MAX_REVIEW_SECONDS",
    "FOCUSPROOF_LLM_MAX_CONCURRENT_REVIEWS",
    "FOCUSPROOF_LLM_ADMISSION_TIMEOUT_SECONDS",
    "FOCUSPROOF_LLM_MAX_CALLS_PER_REVIEW",
    "FOCUSPROOF_LLM_MAX_COST_USD",
    "FOCUSPROOF_LLM_INPUT_COST_PER_TOKEN",
    "FOCUSPROOF_LLM_OUTPUT_COST_PER_TOKEN",
    "LITELLM_LOCAL_MODEL_COST_MAP",
)


def _migrate(project_root: Path, database_url: str) -> None:
    config = Config(project_root / "alembic.ini")
    config.set_main_option("script_location", str(project_root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _configure_demo_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    media_enabled: bool = False,
) -> None:
    for key in (
        "FOCUSPROOF_PROFILE",
        "FOCUSPROOF_DATA_DIR",
        "FOCUSPROOF_MEDIA_ENABLED",
        "FOCUSPROOF_MEDIA_SCANNER_MODE",
        "FOCUSPROOF_CLAMD_DEFINITIONS_VERSION",
        "FOCUSPROOF_CLAMD_DEFINITIONS_FRESH_AT",
        *_PROVIDER_ENV_KEYS,
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "demo-deterministic")
    monkeypatch.setenv("FOCUSPROOF_DATA_DIR", str(tmp_path))
    if media_enabled:
        monkeypatch.setenv("FOCUSPROOF_MEDIA_ENABLED", "true")
        monkeypatch.setenv("FOCUSPROOF_MEDIA_SCANNER_MODE", "fake-clean")
        monkeypatch.setenv("FOCUSPROOF_CLAMD_DEFINITIONS_VERSION", "deterministic-test")
        monkeypatch.setenv("FOCUSPROOF_CLAMD_DEFINITIONS_FRESH_AT", "2026-08-26T00:00:00+00:00")
    else:
        monkeypatch.setenv("FOCUSPROOF_MEDIA_ENABLED", "false")


def _migrated_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    media_enabled: bool = False,
) -> tuple[FastAPI, str]:
    project_root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'demo-deterministic.sqlite3'}"
    _configure_demo_environment(monkeypatch, tmp_path, media_enabled=media_enabled)
    _migrate(project_root, database_url)
    return create_app(database_url=database_url, data_dir=tmp_path), database_url


def _create_session(client: TestClient, *, title: str = "Replay") -> str:
    response = client.post(
        "/sessions",
        json={"domain": "general", "title": title, "goal": "Explain deterministic replay."},
    )
    assert response.status_code == 200
    return str(response.json()["sessionId"])


def _submit_text_evidence(client: TestClient, session_id: str, text: str) -> None:
    response = client.post(
        f"/sessions/{session_id}/evidence",
        json={"evidenceType": "text", "textContent": text},
    )
    assert response.status_code == 200


def _native_message_keys(application: FastAPI, session_id: str) -> list[str]:
    handle = application.state.conversation_manager.get(session_id)
    return [
        key
        for event in handle.conversation.state.events
        if isinstance(event, MessageEvent)
        if (key := message_key_from_event(event)) is not None
    ]


def _native_event_ids(application: FastAPI, session_id: str) -> list[str]:
    handle = application.state.conversation_manager.get(session_id)
    return [str(event.id) for event in handle.conversation.state.events]


def test_demo_deterministic_completes_second_review_in_same_native_conversation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, _ = _migrated_app(tmp_path, monkeypatch)
    evidence_text = "Append-only replay reapplies the same immutable history."
    answer_text = "The original ordered events stay available, so replay can deterministically rebuild the same state."

    with TestClient(application, raise_server_exceptions=False) as client:
        session_id = _create_session(client, title="Demo deterministic same process")
        _submit_text_evidence(client, session_id, evidence_text)

        first_review = client.post(f"/sessions/{session_id}/review")
        assert first_review.status_code == 200
        first_payload = first_review.json()
        assert first_payload["reviewStatus"] == "awaiting_user"
        conversation_id = first_payload["conversationId"]
        question_id = first_payload["agentQuestions"][0]["questionId"]
        before_ids = _native_event_ids(application, session_id)

        answer = client.post(
            f"/sessions/{session_id}/answer",
            json={"questionId": question_id, "answer": answer_text},
        )
        assert answer.status_code == 200
        second_review = client.post(f"/sessions/{session_id}/review")
        assert second_review.status_code == 200, (
            "expected second review to complete in demo-deterministic profile, "
            f"got status={second_review.status_code} body={second_review.text}"
        )
        second_payload = second_review.json()
        assert second_payload["reviewStatus"] == "completed"
        assert second_payload["reviewResult"] is not None
        assert second_payload["conversationId"] == conversation_id

        after_ids = _native_event_ids(application, session_id)
        assert after_ids[: len(before_ids)] == before_ids
        assert len(after_ids) > len(before_ids)

        state = client.get(f"/sessions/{session_id}")
        assert state.status_code == 200
        serialized = json.dumps(
            {
                "review": second_payload,
                "state": state.json(),
                "events": client.get(f"/sessions/{session_id}/events").json(),
            },
            sort_keys=True,
        )
        assert evidence_text in serialized
        assert answer_text in serialized
        assert "TestLLMExhaustedError" not in serialized
        assert "runtime_unavailable" not in serialized

        native_keys = _native_message_keys(application, session_id)
        assert native_keys.count(f"goal:{session_id}") == 1
        assert sum(key.startswith("evidence:") for key in native_keys) == 1
        assert sum(key.startswith("answer:") for key in native_keys) == 1


def test_demo_deterministic_restores_awaiting_user_and_finishes_without_duplicate_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_1, database_url = _migrated_app(tmp_path, monkeypatch)
    evidence_text = "Stable identities let replay continue across restart."

    with TestClient(application_1, raise_server_exceptions=False) as client:
        session_id = _create_session(client, title="Demo deterministic restart")
        _submit_text_evidence(client, session_id, evidence_text)
        first_review = client.post(f"/sessions/{session_id}/review")
        assert first_review.status_code == 200
        first_payload = first_review.json()
        assert first_payload["reviewStatus"] == "awaiting_user"
        question_id = first_payload["agentQuestions"][0]["questionId"]
        conversation_id = first_payload["conversationId"]
        before_ids = _native_event_ids(application_1, session_id)

    application_2 = create_app(database_url=database_url, data_dir=tmp_path)
    with TestClient(application_2, raise_server_exceptions=False) as client:
        answer = client.post(
            f"/sessions/{session_id}/answer",
            json={
                "questionId": question_id,
                "answer": "Reusing the same conversation keeps the persisted event history continuous.",
            },
        )
        assert answer.status_code == 200
        completed_review = client.post(f"/sessions/{session_id}/review")
        assert completed_review.status_code == 200, (
            "expected restarted demo-deterministic review to complete, "
            f"got status={completed_review.status_code} body={completed_review.text}"
        )
        completed_payload = completed_review.json()
        assert completed_payload["reviewStatus"] == "completed"
        assert completed_payload["conversationId"] == conversation_id
        assert completed_payload["reviewResult"] is not None

        after_ids = _native_event_ids(application_2, session_id)
        assert after_ids[: len(before_ids)] == before_ids
        native_keys = _native_message_keys(application_2, session_id)
        assert native_keys.count(f"goal:{session_id}") == 1
        assert sum(key.startswith("evidence:") for key in native_keys) == 1
        assert sum(key.startswith("answer:") for key in native_keys) == 1

        events = client.get(f"/sessions/{session_id}/events")
        assert events.status_code == 200
        event_types = [event["type"] for event in events.json()["events"]]
        assert event_types.count("question.asked") == 1
        assert event_types.count("review.completed") == 1
        assert event_types.count("score.calculated") == 1
        assert "TestLLMExhaustedError" not in json.dumps(events.json(), sort_keys=True)

