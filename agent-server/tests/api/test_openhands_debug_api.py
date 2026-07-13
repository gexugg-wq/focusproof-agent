from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from focusproof.api.app import app
from focusproof.openhands_adapter import real_conversation


def _assert_no_secret_payload(payload: object) -> None:
    text = repr(payload).lower()
    assert "api_key" not in text
    assert "apikey" not in text
    assert "sk-" not in text
    assert "dashscope_api_key" not in text


def test_debug_env_status_does_not_return_api_key() -> None:
    response = TestClient(app).get("/debug/openhands/env-status")

    assert response.status_code == 200
    data = response.json()
    assert "envFileExists" in data
    _assert_no_secret_payload(data)


def test_debug_llm_status_does_not_return_api_key() -> None:
    response = TestClient(app).get("/debug/openhands/llm-status")

    assert response.status_code == 200
    data = response.json()
    assert "canBuildConfig" in data
    _assert_no_secret_payload(data)


def test_debug_conversation_test_is_controlled_without_credential(
    monkeypatch: MonkeyPatch,
) -> None:
    def unavailable_runner(goal: str, evidence: str, domain: str) -> dict[str, object]:
        return {
            "mode": "unavailable",
            "model": None,
            "domain": domain,
            "recommendedAction": None,
            "question": None,
            "reason": None,
            "rawText": None,
            "disabledTools": ["TerminalTool", "FileEditorTool"],
            "error": "missing credential or invalid dotenv configuration",
        }

    monkeypatch.setattr(real_conversation, "run_real_learning_review_spike", unavailable_runner)

    response = TestClient(app).post(
        "/debug/openhands/conversation-test",
        json={"domain": "web3", "goal": "Understand tx hash", "evidence": "hash 0x1234567890"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "unavailable"
    assert "disabledTools" in data
    _assert_no_secret_payload(data)


def test_debug_conversation_test_can_return_fake_runner(monkeypatch: MonkeyPatch) -> None:
    def fake_runner(goal: str, evidence: str, domain: str) -> dict[str, object]:
        return {
            "mode": "real",
            "model": "fake-model",
            "domain": domain,
            "recommendedAction": "ask_question",
            "question": "What does the hash identify?",
            "reason": "The evidence needs explanation.",
            "rawText": "ask_question",
            "disabledTools": ["TerminalTool"],
            "error": None,
        }

    monkeypatch.setattr(real_conversation, "run_real_learning_review_spike", fake_runner)

    response = TestClient(app).post(
        "/debug/openhands/conversation-test",
        json={"domain": "web3", "goal": "Understand tx hash", "evidence": "hash 0x1234567890"},
    )

    assert response.status_code == 200
    assert response.json()["recommendedAction"] == "ask_question"
