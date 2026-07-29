from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest

from focusproof.api import app as app_module


SECRET = "provider-secret-sentinel"
EVIDENCE = "evidence-body-sentinel"


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


def test_maintenance_lock_rejects_writes_but_keeps_health_available(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    data_dir = tmp_path / "openhands"
    data_dir.mkdir()
    (data_dir / app_module.MAINTENANCE_LOCK_NAME).write_text("locked\n", encoding="ascii")
    app = app_module.create_app(
        data_dir=data_dir,
        database_url=f"sqlite+pysqlite:///{data_dir / 'focusproof.sqlite3'}",
    )
    caplog.set_level(logging.INFO, logger="focusproof.operations")

    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        rejected = client.post("/sessions", json={"evidence": SECRET})

    assert health.status_code == 200
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
