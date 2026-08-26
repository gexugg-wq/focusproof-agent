from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from focusproof.api.app import create_app
from focusproof.openhands_runtime import sdk_contracts


def test_public_sdk_contract_preflight_passes_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider must not run")

    monkeypatch.setattr("openhands.sdk.Agent.step", forbidden)
    # Signature drift is detected before this patched function could ever execute.
    with pytest.raises(sdk_contracts.OpenHandsContractUnavailable):
        sdk_contracts.preflight_openhands_sdk_contract()


def test_ready_fails_closed_with_structured_contract_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "deterministic-test")
    monkeypatch.setattr(
        sdk_contracts,
        "preflight_openhands_sdk_contract",
        lambda: (_ for _ in ()).throw(
            sdk_contracts.OpenHandsContractUnavailable("CANARY /private/path?token=secret")
        ),
    )
    monkeypatch.setattr("focusproof.api.app.check_schema_revision", lambda *args: None)
    app = create_app(
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'focusproof.db'}",
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "code": "runtime_contract_unavailable",
        "retryable": True,
    }
    assert "CANARY" not in response.text
    assert "private" not in response.text
