from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "run_general_core_gate.py"


def load_gate() -> Any:
    spec = importlib.util.spec_from_file_location("run_general_core_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fake_http_replays_two_official_scenarios_and_passes(tmp_path: Path) -> None:
    gate = load_gate()
    calls: list[tuple[str, str]] = []
    payloads: list[dict[str, Any] | None] = []
    def scenario_responses(session: str, question: str) -> list[dict[str, Any]]:
        return [
            {"plugins": []},
            {"sessionId": session},
            {"evidenceId": "ev-1"},
            {
                "reviewStatus": "awaiting_user",
                "agentQuestions": [{"questionId": "q-1", "question": question}],
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
                "conversationId": f"conv-{session}",
                "actionEventsCount": 1,
                "observationEventsCount": 1,
            },
            {"events": [{"type": "review.completed", "sequence": 1}]},
            {"state": {"conversationId": f"conv-{session}"}, "view": {"pluginCapabilities": []}},
        ]
    responses = iter(scenario_responses("sess-1", "Why chlorophyll?") + scenario_responses("sess-2", "What is lexical scope?")[1:])

    def fake_request(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, url.removeprefix("http://127.0.0.1:8765")))
        payloads.append(kwargs.get("payload"))
        return next(responses)

    report_path = tmp_path / "report.json"
    result = gate.run_gate(
        base_url="http://127.0.0.1:8765",
        scenarios=gate.SCENARIOS,
        request=fake_request,
        report_path=report_path,
        environ={"FOCUSPROOF_LLM_API_KEY": "super-secret"},
        platform_name="linux",
    )

    assert result == 0
    assert [path for _, path in calls][:8] == [
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
    assert [item["question"] for item in report["scenarios"]] == ["Why chlorophyll?", "What is lexical scope?"]
    assert report["scenarios"][0]["buildLog"][0]["type"] == "review.completed"
    assert report["scenarios"][1]["conversationId"] == "conv-sess-2"
    assert calls[9][1] == "/sessions/sess-2/evidence"
    assert payloads[9] == {
        "evidenceType": "url",
        "sourceUrl": "https://docs.python.org/3/tutorial/classes.html#python-scopes-and-namespaces",
        "textContent": (
            "The Python documentation explains that a closure keeps access to names "
            "bound in its enclosing lexical scope after the outer function returns."
        ),
    }
    assert "super-secret" not in report_path.read_text(encoding="utf-8")


def test_direct_completion_without_questions_preserves_complete_scenario(tmp_path: Path) -> None:
    gate = load_gate()
    responses = iter([
        {"plugins": []}, {"sessionId": "sess-direct"}, {"evidenceId": "ev-direct"},
        {
            "reviewStatus": "completed", "score": 0.92, "reason": "grounded",
            "confidence": 0.89, "findings": ["complete evidence"], "summary": "summary",
            "nextStep": "next", "conversationId": "conv-direct",
            "actionEventsCount": 1, "observationEventsCount": 1,
        },
        {"events": [{"type": "review.completed", "sequence": 1}]},
        {"state": {"conversationId": "conv-direct"}, "view": {"pluginCapabilities": []}},
    ])
    path = tmp_path / "direct.json"

    code = gate.run_gate(
        base_url="http://x", scenarios=[gate.SCENARIOS[0]],
        request=lambda *args, **kwargs: next(responses), report_path=path,
        environ={}, platform_name="linux",
    )

    assert code == 0
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["overall"] == "PASS"
    assert report["scenarios"][0]["question"] is None
    assert report["scenarios"][0]["questions"] == []
    assert report["scenarios"][0]["conversationId"] == "conv-direct"
    assert report["scenarios"][0]["buildLog"] == [
        {"type": "review.completed", "sequence": 1}
    ]


def test_two_direct_completions_fail_with_redacted_report(tmp_path: Path) -> None:
    gate = load_gate()

    def direct(session: str) -> list[dict[str, Any]]:
        return [
            {"sessionId": session}, {"evidenceId": f"ev-{session}"},
            {
                "reviewStatus": "completed", "score": 0.9, "reason": "grounded",
                "confidence": 0.8, "findings": ["complete"], "summary": "summary",
                "nextStep": "next", "conversationId": f"conv-{session}",
                "actionEventsCount": 1, "observationEventsCount": 1,
            },
            {"events": [{"type": "review.completed"}]},
            {"state": {"conversationId": f"conv-{session}"}, "view": {"pluginCapabilities": []}},
        ]

    responses = iter([{"plugins": []}, *direct("one"), *direct("two")])
    path = tmp_path / "no-questions.json"
    code = gate.run_gate(
        base_url="http://x", scenarios=gate.SCENARIOS,
        request=lambda *args, **kwargs: next(responses), report_path=path,
        environ={"FOCUSPROOF_LLM_API_KEY": "super-secret"}, platform_name="linux",
    )

    assert code == 1
    text = path.read_text(encoding="utf-8")
    report = json.loads(text)
    assert report["overall"] == "FAIL"
    assert [item["question"] for item in report["scenarios"][:2]] == [None, None]
    assert report["scenarios"][-1]["status"] == "FAIL"
    assert "super-secret" not in text


def test_direct_then_questioned_completion_passes_with_second_scenario_question(tmp_path: Path) -> None:
    gate = load_gate()
    completed = {
        "reviewStatus": "completed", "score": 0.9, "reason": "grounded",
        "confidence": 0.8, "findings": ["complete"], "summary": "summary",
        "nextStep": "next", "actionEventsCount": 1, "observationEventsCount": 1,
    }
    responses = iter([
        {"plugins": []},
        {"sessionId": "direct"}, {"evidenceId": "ev-direct"},
        {**completed, "conversationId": "conv-direct"},
        {"events": [{"type": "review.completed"}]},
        {"state": {"conversationId": "conv-direct"}, "view": {"pluginCapabilities": []}},
        {"sessionId": "questioned"}, {"evidenceId": "ev-questioned"},
        {"reviewStatus": "awaiting_user", "agentQuestions": [
            {"questionId": "q-second", "question": "What state does the closure retain?"}
        ]},
        {"reviewStatus": "awaiting_user"},
        {**completed, "conversationId": "conv-questioned"},
        {"events": [{"type": "review.completed"}]},
        {"state": {"conversationId": "conv-questioned"}, "view": {"pluginCapabilities": []}},
    ])
    path = tmp_path / "mixed.json"

    code = gate.run_gate(
        base_url="http://x", scenarios=gate.SCENARIOS,
        request=lambda *args, **kwargs: next(responses), report_path=path,
        environ={}, platform_name="linux",
    )

    assert code == 0
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["overall"] == "PASS"
    assert [item["question"] for item in report["scenarios"]] == [
        None, "What state does the closure retain?"
    ]
    assert report["scenarios"][1]["questions"] == ["What state does the closure retain?"]


@pytest.mark.parametrize("actions,observations", [(0, 1), (1, 0)])
def test_missing_native_action_or_observation_fails(tmp_path: Path, actions: int, observations: int) -> None:
    gate = load_gate()
    responses = iter([
        {"plugins": []}, {"sessionId": "s"}, {"evidenceId": "e"},
        {"reviewStatus": "awaiting_user", "agentQuestions": [{"questionId": "q", "question": "dynamic"}]}, {},
        {"reviewStatus": "completed", "score": 1, "reason": "ok", "confidence": 1, "findings": [], "summary": "s", "nextStep": "n", "conversationId": "c", "actionEventsCount": actions, "observationEventsCount": observations},
        {"events": [{"type": "review.completed"}]}, {"state": {"conversationId": "c"}, "view": {"pluginCapabilities": []}},
    ])
    path = tmp_path / "report.json"
    assert gate.run_gate(base_url="http://x", scenarios=[gate.SCENARIOS[0]], request=lambda *a, **k: next(responses), report_path=path, environ={}, platform_name="linux") == 1
    assert json.loads(path.read_text())["overall"] == "FAIL"


def test_empty_build_log_fails(tmp_path: Path) -> None:
    gate = load_gate()
    responses = iter([
        {"plugins": []}, {"sessionId": "s"}, {"evidenceId": "e"},
        {"reviewStatus": "awaiting_user", "agentQuestions": [{"questionId": "q", "question": "dynamic"}]}, {},
        {"reviewStatus": "completed", "score": 1, "reason": "ok", "confidence": 1, "findings": [], "summary": "s", "nextStep": "n", "conversationId": "c", "actionEventsCount": 1, "observationEventsCount": 1},
        {"events": []}, {"state": {"conversationId": "c"}, "view": {"pluginCapabilities": []}},
    ])
    path = tmp_path / "report.json"
    assert gate.run_gate(base_url="http://x", scenarios=[gate.SCENARIOS[0]], request=lambda *a, **k: next(responses), report_path=path, environ={}, platform_name="linux") == 1


def test_deadline_is_passed_to_http_and_interaction_expiry_fails(tmp_path: Path) -> None:
    gate = load_gate()
    ticks = iter([0.0, 1.0, 12.0])
    timeouts: list[float] = []
    def request(*args: Any, **kwargs: Any) -> dict[str, Any]:
        timeouts.append(kwargs["timeout_seconds"])
        return {"plugins": []} if len(timeouts) == 1 else {"sessionId": "s"}
    path = tmp_path / "report.json"
    assert gate.run_gate(base_url="http://x", scenarios=[gate.SCENARIOS[0]], request=request, report_path=path, environ={}, platform_name="linux", clock=lambda: next(ticks), total_timeout_seconds=10) == 1
    assert timeouts == [9.0]


def test_http_500_is_fail_but_structured_503_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = load_gate()
    def error(code: int, body: bytes) -> None:
        monkeypatch.setattr(gate, "urlopen", lambda *a, **k: (_ for _ in ()).throw(HTTPError("http://x", code, "x", {}, BytesIO(body))))
    error(500, b'{"code":"internal_error","secret":"do-not-report"}')
    with pytest.raises(gate.BusinessFailure):
        gate.request_json("GET", "http://x")
    error(503, b'{"code":"runtime_unavailable","secret":"do-not-report"}')
    with pytest.raises(gate.ProviderBlocked, match="runtime_unavailable"):
        gate.request_json("GET", "http://x")


def test_connection_reset_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = load_gate()
    monkeypatch.setattr(gate, "urlopen", lambda *a, **k: (_ for _ in ()).throw(ConnectionResetError()))
    with pytest.raises(gate.ProviderBlocked, match="network unavailable"):
        gate.request_json("GET", "http://x")


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


def test_server_environment_is_tmp_isolated_and_removed_plugin_env_absent(tmp_path: Path) -> None:
    gate = load_gate()
    source = {"FOCUSPROOF_LLM_API_KEY": "secret", "HOME": "/home/holy"}

    child = gate.server_environment(source, tmp_path)

    assert child["FOCUSPROOF_DATA_DIR"] == str(tmp_path / "data")
    assert child["FOCUSPROOF_DATABASE_URL"].startswith("sqlite:///")
    assert child["DATABASE_URL"] == child["FOCUSPROOF_DATABASE_URL"]
    removed = ("MO" + "NAD").upper()
    assert all(removed not in key.upper() for key in child)
    assert child["LITELLM_MODE"] == "PRODUCTION"
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


def test_cli_starts_real_server_migrates_and_blocks_at_official_review(tmp_path: Path) -> None:
    sentinel = "must-not-load-from-dotenv"
    (tmp_path / ".env").write_text(
        f"FOCUSPROOF_LLM_API_KEY={sentinel}\nFOCUSPROOF_LLM_MODEL={sentinel}\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("FOCUSPROOF_LLM_")
        and key not in {"OPENAI_API_KEY", "DASHSCOPE_API_KEY", "OPENHANDS_LLM_MODEL"}
    }
    env["PYTHONPATH"] = str(SCRIPT.parents[1] / "agent-server")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--report", str(report_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    assert completed.returncode == 2
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["overall"] == "BLOCKED"
    assert "HTTP 503" in report["scenarios"][-1]["reason"]
    assert "failed to start" not in report_text
    assert sentinel not in report_text + completed.stdout + completed.stderr
