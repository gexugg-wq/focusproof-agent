from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "run_general_core_gate.py"


def load_gate() -> Any:
    spec = importlib.util.spec_from_file_location("run_general_core_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fake_http_replays_official_routes_and_passes(tmp_path: Path) -> None:
    gate = load_gate()
    calls: list[tuple[str, str]] = []
    responses = iter(
        [
            {"plugins": []},
            {"sessionId": "sess-1"},
            {"evidenceId": "ev-1"},
            {
                "reviewStatus": "awaiting_user",
                "agentQuestions": [{"questionId": "q-1", "question": "Why chlorophyll?"}],
            },
            {"reviewStatus": "awaiting_user"},
            {
                "reviewStatus": "completed",
                "score": 0.91,
                "reason": "grounded",
                "confidence": 0.88,
                "findings": ["explained mechanism"],
                "summary": "independent explanation",
                "nextStep": "compare limiting factors",
                "conversationId": "conv-1",
            },
            {"events": [{"type": "ActionEvent"}, {"type": "ObservationEvent"}]},
            {"buildLog": [{"type": "review_completed"}]},
        ]
    )

    def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        calls.append((method, url.removeprefix("http://127.0.0.1:8765")))
        return next(responses)

    report_path = tmp_path / "report.json"
    result = gate.run_gate(
        base_url="http://127.0.0.1:8765",
        scenarios=[gate.SCENARIOS[0]],
        request=fake_request,
        report_path=report_path,
        environ={"FOCUSPROOF_LLM_API_KEY": "super-secret"},
        platform_name="linux",
    )

    assert result == 0
    assert [path for _, path in calls] == [
        "/openhands/capabilities",
        "/sessions",
        "/sessions/sess-1/evidence",
        "/sessions/sess-1/review",
        "/sessions/sess-1/answer",
        "/sessions/sess-1/review",
        "/sessions/sess-1/events",
        "/sessions/sess-1",
    ]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(report) == {
        "schemaVersion",
        "timestamp",
        "gitSha",
        "provider",
        "model",
        "scenarios",
        "overall",
    }
    assert report["overall"] == "PASS"
    assert report["scenarios"][0]["question"] == "Why chlorophyll?"
    assert "super-secret" not in report_path.read_text(encoding="utf-8")


def test_provider_failure_is_blocked_and_redacted(tmp_path: Path) -> None:
    gate = load_gate()

    def unavailable(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        raise gate.ProviderBlocked("credential rejected: token=super-secret")

    report_path = tmp_path / "blocked.json"
    result = gate.run_gate(
        base_url="http://127.0.0.1:8765",
        scenarios=[gate.SCENARIOS[0]],
        request=unavailable,
        report_path=report_path,
        environ={"FOCUSPROOF_LLM_API_KEY": "super-secret"},
        platform_name="linux",
    )

    assert result != 0
    text = report_path.read_text(encoding="utf-8")
    assert json.loads(text)["overall"] == "BLOCKED"
    assert "super-secret" not in text


def test_windows_is_rejected_without_http(tmp_path: Path) -> None:
    gate = load_gate()
    called = False

    def request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called
        del args, kwargs
        called = True
        return {}

    with pytest.raises(gate.GateConfigurationError, match="Linux"):
        gate.run_gate(
            base_url="http://127.0.0.1:8765",
            scenarios=[gate.SCENARIOS[0]],
            request=request,
            report_path=tmp_path / "unused.json",
            environ={},
            platform_name="win32",
        )
    assert called is False


def test_business_assertion_failure_is_fail(tmp_path: Path) -> None:
    gate = load_gate()
    responses = iter([{"plugins": []}, {"sessionId": "sess-1"}, {"evidenceId": "ev-1"}, {"reviewStatus": "failed"}])

    report_path = tmp_path / "failed.json"
    code = gate.run_gate(
        base_url="http://127.0.0.1:8765",
        scenarios=[gate.SCENARIOS[0]],
        request=lambda *args, **kwargs: next(responses),
        report_path=report_path,
        environ={},
        platform_name="linux",
    )

    assert code == 1
    assert json.loads(report_path.read_text(encoding="utf-8"))["overall"] == "FAIL"


def test_server_environment_is_tmp_isolated_and_monad_disabled(tmp_path: Path) -> None:
    gate = load_gate()
    source = {"FOCUSPROOF_LLM_API_KEY": "secret", "HOME": "/home/holy"}

    child = gate.server_environment(source, tmp_path)

    assert child["FOCUSPROOF_DATA_DIR"] == str(tmp_path / "data")
    assert child["FOCUSPROOF_DATABASE_URL"].startswith("sqlite:///")
    assert child["DATABASE_URL"] == child["FOCUSPROOF_DATABASE_URL"]
    assert child["FOCUSPROOF_PLUGIN_MONAD_ENABLED"] == "false"
    assert child["FOCUSPROOF_LLM_API_KEY"] == "secret"
    assert "DOTENV" not in child


def test_blocked_report_can_be_written_before_http_gate(tmp_path: Path) -> None:
    gate = load_gate()
    path = tmp_path / "startup-blocked.json"

    code = gate.write_terminal_report(
        path, "BLOCKED", "server startup failed: super-secret", {"API_TOKEN": "super-secret"}
    )

    assert code == 2
    text = path.read_text(encoding="utf-8")
    assert json.loads(text)["overall"] == "BLOCKED"
    assert "super-secret" not in text
