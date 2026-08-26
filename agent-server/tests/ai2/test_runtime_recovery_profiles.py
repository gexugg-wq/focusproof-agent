from __future__ import annotations

from pathlib import Path
from typing import cast

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from openhands.sdk import LLM
from openhands.sdk.testing import TestLLM
import pytest
from pydantic import ValidationError

from focusproof.api import app as app_module
from focusproof.api.app import create_app
from focusproof.config.profiles import load_runtime_settings
from focusproof.openhands_runtime.factory import RuntimeUnavailableError


def _provider_environment(profile: str = "demo-real-vision") -> dict[str, str]:
    return {
        "FOCUSPROOF_PROFILE": profile,
        "FOCUSPROOF_LLM_PROVIDER": "openai-compatible",
        "FOCUSPROOF_LLM_MODEL": "openai/vision-model",
        "FOCUSPROOF_LLM_SUPPORTS_VISION": "true",
        "FOCUSPROOF_LLM_BASE_URL": "https://provider.example.test/v1",
        "FOCUSPROOF_LLM_API_KEY": "placeholder-secret",
        "FOCUSPROOF_LLM_REQUEST_TIMEOUT_SECONDS": "30",
        "FOCUSPROOF_LLM_NUM_RETRIES": "0",
        "FOCUSPROOF_LLM_RETRY_MIN_WAIT_SECONDS": "0",
        "FOCUSPROOF_LLM_RETRY_MAX_WAIT_SECONDS": "0",
        "FOCUSPROOF_LLM_CONTEXT_WINDOW_TOKENS": "16384",
        "FOCUSPROOF_LLM_MAX_OUTPUT_TOKENS": "1024",
        "FOCUSPROOF_LLM_MAX_ITERATIONS": "2",
        "FOCUSPROOF_LLM_MAX_REVIEW_SECONDS": "30",
        "FOCUSPROOF_LLM_MAX_CONCURRENT_REVIEWS": "1",
        "FOCUSPROOF_LLM_ADMISSION_TIMEOUT_SECONDS": "1",
        "FOCUSPROOF_LLM_MAX_CALLS_PER_REVIEW": "2",
        "FOCUSPROOF_LLM_MAX_COST_USD": "0.05",
        "FOCUSPROOF_LLM_INPUT_COST_PER_TOKEN": "0",
        "FOCUSPROOF_LLM_OUTPUT_COST_PER_TOKEN": "0",
        "LITELLM_LOCAL_MODEL_COST_MAP": "true",
    }


def _migrated_app(tmp_path: Path) -> FastAPI:
    root = Path(__file__).resolve().parents[3]
    url = f"sqlite+pysqlite:///{tmp_path / 'recovery.sqlite3'}"
    config = Config(root / "alembic.ini")
    config.set_main_option("script_location", str(root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_app(database_url=url, data_dir=tmp_path)


def _configure_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    environment: dict[str, str],
) -> None:
    for key in tuple(_provider_environment()) + (
        "FOCUSPROOF_OIDC_ISSUER",
        "FOCUSPROOF_OIDC_AUDIENCE",
        "FOCUSPROOF_OIDC_JWKS_URI",
        "FOCUSPROOF_OIDC_FINGERPRINT_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FOCUSPROOF_DATA_DIR", str(tmp_path))


def _create_session(client: TestClient) -> Response:
    return cast(
        Response,
        client.post(
            "/sessions",
            json={"domain": "general", "title": "Replay", "goal": "Explain replay"},
        ),
    )


def test_demo_deterministic_is_explicit_and_never_builds_real_provider() -> None:
    settings = load_runtime_settings(_provider_environment("demo-deterministic"))
    assert settings.profile == "demo-deterministic"
    assert settings.real_llm is None


def test_demo_real_vision_missing_provider_configuration_fails_closed() -> None:
    with pytest.raises(ValidationError, match="FOCUSPROOF_LLM_MODEL"):
        load_runtime_settings({"FOCUSPROOF_PROFILE": "demo-real-vision"})


def test_demo_real_vision_ready_configuration_preserves_vision_and_redacts_secret() -> None:
    settings = load_runtime_settings(_provider_environment())
    assert settings.profile == "demo-real-vision"
    assert settings.real_llm is not None and settings.real_llm.supports_vision is True
    assert "placeholder-secret" not in settings.model_dump_json()


def test_missing_real_provider_is_unavailable_before_session_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "demo-real-vision")
    for key in _provider_environment():
        if key != "FOCUSPROOF_PROFILE":
            monkeypatch.delenv(key, raising=False)
    app = _migrated_app(tmp_path)
    with TestClient(app) as client:
        ready = client.get("/ready")
        response = client.post(
            "/sessions",
            json={"domain": "general", "title": "Replay", "goal": "Explain replay"},
        )
    assert ready.status_code == 503
    assert ready.json() == {"code": "runtime_unavailable", "retryable": True}
    assert response.status_code == 503
    assert response.json() == {"code": "runtime_unavailable", "retryable": True}


def test_demo_deterministic_lifespan_is_anonymous_and_uses_official_test_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_profile(
        monkeypatch,
        tmp_path,
        {"FOCUSPROOF_PROFILE": "demo-deterministic"},
    )
    official_factory = app_module.staging_test_llm
    produced: list[LLM] = []

    def factory_spy(session_id: str) -> LLM:
        llm = cast(LLM, official_factory(session_id))
        produced.append(llm)
        return llm

    monkeypatch.setattr(app_module, "staging_test_llm", factory_spy)
    app = _migrated_app(tmp_path)
    with TestClient(app) as client:
        ready = client.get("/ready")
        response = _create_session(client)
        state = client.get(f"/sessions/{response.json()['sessionId']}")

    assert ready.status_code == 200
    assert response.status_code == 200
    assert state.status_code == 200
    assert state.json()["state"]["runtimeMode"] == "openhands-local-scripted-test"
    assert produced and all(isinstance(llm, TestLLM) for llm in produced)


def test_demo_real_vision_lifespan_is_anonymous_real_and_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_profile(monkeypatch, tmp_path, _provider_environment())
    provider_builds: list[str] = []

    def provider_spy(policy: object, *, usage_id: str) -> LLM:
        del policy
        provider_builds.append(usage_id)
        raise RuntimeUnavailableError("stop before any provider request")

    def forbidden_fallback(*args: object, **kwargs: object) -> TestLLM:
        del args, kwargs
        raise AssertionError("demo-real-vision must not construct TestLLM")

    monkeypatch.setattr(
        "focusproof.openhands_runtime.factory.build_openhands_llm", provider_spy
    )
    monkeypatch.setattr(TestLLM, "from_messages", forbidden_fallback)
    app = _migrated_app(tmp_path)
    with TestClient(app) as client:
        ready = client.get("/ready")
        response = _create_session(client)
        state = client.get(f"/sessions/{response.json()['sessionId']}")

    assert ready.status_code == 200
    assert response.status_code == 200
    assert state.status_code == 200
    assert state.json()["state"]["runtimeMode"] == "openhands-local-real"
    assert provider_builds == [f"focusproof-{response.json()['sessionId']}"]


@pytest.mark.parametrize("profile", ["demo-deterministic", "demo-real-vision"])
def test_demo_profile_invalid_oidc_is_identity_unavailable_not_runtime(
    profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = (
        {"FOCUSPROOF_PROFILE": profile}
        if profile == "demo-deterministic"
        else _provider_environment(profile)
    )
    environment["FOCUSPROOF_OIDC_ISSUER"] = "http://invalid.example.test"
    _configure_profile(monkeypatch, tmp_path, environment)
    app = _migrated_app(tmp_path)

    with TestClient(app) as client:
        ready = client.get("/ready")
        response = _create_session(client)

    expected = {"code": "identity_unavailable", "retryable": False}
    assert ready.status_code == 503
    assert ready.json() == expected
    assert response.status_code == 503
    assert response.json() == expected
