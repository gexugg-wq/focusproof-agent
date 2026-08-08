from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openhands.sdk.testing import TestLLM
import pytest

from focusproof.api import app as app_module
from focusproof.openhands_runtime.provider_admission import (
    ProviderAdmissionUnavailableError,
)
from focusproof.recovery import maintenance_window


SECRET = "provider-secret-sentinel"
EVIDENCE = "evidence-body-sentinel"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _enable_operations_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    # Alembic's logging fileConfig disables pre-existing named loggers. The
    # application runs migrations out of process, so telemetry tests restore
    # their logger explicitly when migration tests share this pytest process.
    monkeypatch.setattr(app_module.OPERATIONS_LOGGER, "disabled", False)


def test_operational_event_accepts_only_bounded_safe_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="focusproof.operations")

    app_module._emit_operational_event(
        "request",
        route="/sessions/{session_id}/review",
        status="completed",
        latency_ms=12,
    )

    payload = json.loads(caplog.records[-1].message)
    assert payload == {
        "event": "request",
        "latency_ms": 12,
        "route": "/sessions/{session_id}/review",
        "status": "completed",
    }
    with pytest.raises(ValueError, match="field"):
        app_module._emit_operational_event("request", evidence=EVIDENCE)
    assert SECRET not in caplog.text
    assert EVIDENCE not in caplog.text


def test_review_signal_uses_official_conversation_provider_aggregate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="focusproof.operations")
    snapshot = SimpleNamespace(
        call_count=2,
        input_tokens=30,
        output_tokens=10,
        cost_usd=0.0125,
        latency_seconds=0.75,
    )
    handle = SimpleNamespace(provider_usage_snapshot=lambda: snapshot)
    manager: Any = SimpleNamespace(get=lambda session_id: handle)
    result: Any = SimpleNamespace(reviewStatus="completed")

    app_module._record_review_operational_signal(
        manager,
        "sess-safe-id",
        result,
        latency_ms=1000,
    )

    payload = json.loads(caplog.records[-1].message)
    assert payload["event"] == "review"
    assert payload["status"] == "completed"
    assert payload["provider_calls"] == 2
    assert payload["provider_input_tokens"] == 30
    assert payload["provider_output_tokens"] == 10
    assert payload["provider_cost_microusd"] == 12500
    assert "sess-safe-id" not in caplog.text


def test_route_template_rejects_malicious_or_unknown_session_tails() -> None:
    assert app_module._bounded_route("/sessions/safe/review") == (
        "/sessions/{session_id}/review"
    )
    assert app_module._bounded_route("/sessions/safe/unknown-user-value") == "unmatched"
    assert app_module._bounded_route("/sessions/safe/%0Aprovider-secret") == "unmatched"
    assert app_module._bounded_route("/user-controlled-root") == "unmatched"


def test_failed_review_and_provider_admission_emit_only_bounded_signals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="focusproof.operations")
    snapshot = SimpleNamespace(
        call_count=1,
        input_tokens=5,
        output_tokens=0,
        cost_usd=0.0,
        latency_seconds=0.2,
    )
    handle = SimpleNamespace(provider_usage_snapshot=lambda: snapshot)
    manager: Any = SimpleNamespace(get=lambda session_id: handle)
    failed: Any = SimpleNamespace(reviewStatus="failed")

    app_module._record_review_operational_signal(
        manager,
        "malicious-session-value",
        failed,
        latency_ms=250,
    )
    app_module._record_review_failure_operational_signal(
        status="rejected",
        outcome="provider_admission",
        latency_ms=20,
    )

    payloads = [json.loads(record.message) for record in caplog.records]
    assert payloads[0]["status"] == "failed"
    assert payloads[0]["outcome"] == "provider_run"
    assert payloads[1] == {
        "event": "review",
        "latency_ms": 20,
        "outcome": "provider_admission",
        "status": "rejected",
    }
    assert "malicious-session-value" not in caplog.text


def test_provider_admission_rejection_from_review_endpoint_is_signaled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_dir = tmp_path / "provider-rejection"
    data_dir.mkdir()
    database_url = f"sqlite+pysqlite:///{data_dir / 'focusproof.sqlite3'}"
    _migrate_database(database_url)
    app_module.OPERATIONS_LOGGER.disabled = False
    app = app_module.create_app(
        data_dir=data_dir,
        database_url=database_url,
        llm_factory=lambda session_id: TestLLM.from_messages([]),
    )
    caplog.set_level(logging.INFO, logger="focusproof.operations")
    app_module.OPERATIONS_LOGGER.addHandler(caplog.handler)

    def reject_provider_admission(*args: Any, **kwargs: Any) -> None:
        raise ProviderAdmissionUnavailableError("bounded rejection")

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            created = client.post(
                "/sessions",
                json={
                    "domain": "general",
                    "title": "Bounded provider admission",
                    "goal": "Prove provider rejection emits a safe signal.",
                },
            )
            assert created.status_code == 200
            session_id = str(created.json()["sessionId"])
            monkeypatch.setattr(
                app.state.conversation_manager,
                "run_review",
                reject_provider_admission,
            )
            rejected = client.post(f"/sessions/{session_id}/review")
    finally:
        app_module.OPERATIONS_LOGGER.removeHandler(caplog.handler)

    assert rejected.status_code == 503
    events = [json.loads(record.message) for record in caplog.records]
    assert {
        "event": "review",
        "latency_ms": next(
            event["latency_ms"]
            for event in events
            if event["event"] == "review"
        ),
        "outcome": "provider_admission",
        "status": "rejected",
    } in events
    assert session_id not in caplog.text


def _migrate_database(database_url: str) -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option(
        "script_location",
        str(PROJECT_ROOT / "agent-server/migrations"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def test_maintenance_lock_rejects_writes_but_keeps_health_available(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_dir = tmp_path / "openhands"
    data_dir.mkdir()
    database_url = f"sqlite+pysqlite:///{data_dir / 'focusproof.sqlite3'}"
    _migrate_database(database_url)
    app_module.OPERATIONS_LOGGER.disabled = False
    app = app_module.create_app(
        data_dir=data_dir,
        database_url=database_url,
    )
    caplog.set_level(logging.INFO, logger="focusproof.operations")
    app_module.OPERATIONS_LOGGER.addHandler(caplog.handler)

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            with maintenance_window(data_dir):
                health = client.get("/health")
                readiness = client.get("/ready")
                rejected = client.post("/sessions", json={"evidence": SECRET})
    finally:
        app_module.OPERATIONS_LOGGER.removeHandler(caplog.handler)

    assert health.status_code == 200
    assert readiness.status_code == 200
    assert rejected.status_code == 503
    assert rejected.json() == {"code": "maintenance_mode", "retryable": True}
    assert SECRET not in caplog.text
    assert EVIDENCE not in caplog.text
    events = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "focusproof.operations"
    ]
    assert any(event["event"] == "admission_rejection" for event in events)
    assert any(event["event"] == "health" for event in events)
