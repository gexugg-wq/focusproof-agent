from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import jwt
from jwt import PyJWKSet
from pydantic import ValidationError
import pytest

from focusproof.api.auth import DEVELOPMENT_USER_ID, VerifiedIdentity
from focusproof.api.oidc import get_token_verifier, require_verified_identity
from focusproof.config.identity import OidcSettings, load_oidc_settings

from .oidc_fixture import (
    LocalOidcFixture,
    StaticPrincipalResolver,
    local_oidc_fixture,
    oidc_test_app,
)


def _session_payload() -> dict[str, object]:
    return {
        "domain": "general",
        "title": "Understand OIDC ownership",
        "goal": "Explain why a verified principal owns one session.",
        "expectedOutput": "A short explanation",
        "plannedMinutes": 20,
    }


def _oidc_env(
    fixture: LocalOidcFixture,
    *,
    profile: str = "staging",
) -> dict[str, str]:
    return {
        "FOCUSPROOF_PROFILE": profile,
        "FOCUSPROOF_OIDC_ISSUER": fixture.issuer,
        "FOCUSPROOF_OIDC_AUDIENCE": fixture.audience,
        "FOCUSPROOF_OIDC_JWKS_URI": "http://testserver/__test__/oidc/jwks",
        "FOCUSPROOF_OIDC_ALLOWED_ALGORITHMS": "RS256",
        "FOCUSPROOF_OIDC_FINGERPRINT_KEY": "placeholder",
    }


def _install_jwks_fetch(
    monkeypatch: pytest.MonkeyPatch,
    *responses: dict[str, object] | Exception,
) -> None:
    response_queue = list(responses)

    def fake_fetch_data(self: jwt.PyJWKClient) -> dict[str, object]:
        if response_queue:
            next_response = response_queue.pop(0)
        else:
            next_response = responses[-1]
        if isinstance(next_response, Exception):
            raise next_response
        jwk_set_cache = getattr(self, "jwk_set_cache", None)
        if jwk_set_cache is not None:
            jwk_set_cache.put(PyJWKSet.from_dict(next_response))
        return next_response

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fake_fetch_data)


def test_local_dev_can_explicitly_keep_anonymous_identity(
) -> None:
    settings = load_oidc_settings({}, profile="local-dev")

    assert isinstance(settings, OidcSettings)
    assert settings.enabled is False
    identity = VerifiedIdentity.model_validate(
        {"principal_id": DEVELOPMENT_USER_ID, "token_fingerprint": "anonymous"}
    )
    assert identity.principal_id == DEVELOPMENT_USER_ID


@pytest.mark.parametrize("profile", ["staging", "production"])
def test_non_local_profiles_require_complete_oidc_configuration(profile: str) -> None:
    with pytest.raises(ValidationError):
        load_oidc_settings({}, profile=profile)


def test_valid_bearer_token_creates_a_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = local_oidc_fixture()
    resolver = StaticPrincipalResolver("principal_oidc_valid")
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    for key, value in _oidc_env(fixture).items():
        monkeypatch.setenv(key, value)
    app = oidc_test_app(
        tmp_path,
        fixture,
        principal_resolver=resolver,
    )

    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            json=_session_payload(),
            headers={"Authorization": f"Bearer {fixture.token()}"},
        )
        session = client.get(
            f"/sessions/{response.json()['sessionId']}",
            headers={"Authorization": f"Bearer {fixture.token()}"},
        )

    assert response.status_code == 200
    assert str(response.json()["sessionId"]).startswith("sess_")
    assert session.status_code == 200
    assert session.json()["state"]["ownerUserId"] == resolver.principal_id


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Basic not-a-bearer",
        "Bearer",
        "Bearer  ",
        "Bearer one two",
    ],
)
def test_business_requests_reject_missing_or_malformed_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorization: str | None,
) -> None:
    fixture = local_oidc_fixture()
    resolver = StaticPrincipalResolver("principal_oidc_reject")
    for key, value in _oidc_env(fixture).items():
        monkeypatch.setenv(key, value)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(
        tmp_path,
        fixture,
        principal_resolver=resolver,
    )

    with TestClient(app) as client:
        headers = {} if authorization is None else {"Authorization": authorization}
        response = client.post("/sessions", json=_session_payload(), headers=headers)

    assert response.status_code == 401
    assert response.json() == {"code": "invalid_token", "retryable": False}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "Bearer " not in response.text


def test_verifier_rejects_expired_future_nbf_wrong_issuer_wrong_audience_and_missing_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = local_oidc_fixture()
    resolver = StaticPrincipalResolver("principal_oidc_claims")
    for key, value in _oidc_env(fixture).items():
        monkeypatch.setenv(key, value)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(
        tmp_path,
        fixture,
        principal_resolver=resolver,
    )

    expired = fixture.token(expires_delta_seconds=-120)
    not_yet_valid = fixture.token(not_before_delta_seconds=300)
    wrong_issuer = fixture.token(issuer="https://other-issuer.example.test")
    wrong_audience = fixture.token(audience="different-audience")
    missing_subject = fixture.token(subject="")

    with TestClient(app) as client:
        tokens = [
            expired,
            not_yet_valid,
            wrong_issuer,
            wrong_audience,
            missing_subject,
        ]
        responses = [
            client.post(
                "/sessions",
                json=_session_payload(),
                headers={"Authorization": f"Bearer {token}"},
            )
            for token in tokens
        ]

    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"code": "invalid_token", "retryable": False}
    assert expired not in caplog.text
    assert wrong_issuer not in caplog.text


def test_verifier_rejects_wrong_kid_bad_signature_and_disallowed_algorithm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_fixture = local_oidc_fixture()
    second_fixture = local_oidc_fixture()
    resolver = StaticPrincipalResolver("principal_oidc_bad_signature")
    for key, value in _oidc_env(first_fixture).items():
        monkeypatch.setenv(key, value)
    _install_jwks_fetch(monkeypatch, {"keys": [first_fixture.public_jwk]})
    app = oidc_test_app(
        tmp_path,
        first_fixture,
        principal_resolver=resolver,
    )

    wrong_kid = second_fixture.token()
    tampered_signature = wrong_kid[:-1] + ("a" if wrong_kid[-1] != "a" else "b")
    disallowed_algorithm = first_fixture.token(algorithm="HS256")

    with TestClient(app) as client:
        responses = [
            client.post(
                "/sessions",
                json=_session_payload(),
                headers={"Authorization": f"Bearer {token}"},
            )
            for token in (wrong_kid, tampered_signature, disallowed_algorithm)
        ]

    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"code": "invalid_token", "retryable": False}


def test_verifier_uses_cached_jwks_then_fails_closed_after_rotation_and_outage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first_fixture = local_oidc_fixture()
    second_fixture = local_oidc_fixture()
    resolver = StaticPrincipalResolver("principal_oidc_cached")
    for key, value in _oidc_env(first_fixture).items():
        monkeypatch.setenv(key, value)
    _install_jwks_fetch(
        monkeypatch,
        {"keys": [first_fixture.public_jwk]},
        RuntimeError("jwks offline"),
        RuntimeError("jwks offline"),
    )
    app = oidc_test_app(
        tmp_path,
        first_fixture,
        principal_resolver=resolver,
    )

    first_token = first_fixture.token()
    second_token = second_fixture.token()

    with TestClient(app) as client:
        first = client.post(
            "/sessions",
            json=_session_payload(),
            headers={"Authorization": f"Bearer {first_token}"},
        )
        cached = client.post(
            "/sessions",
            json=_session_payload(),
            headers={"Authorization": f"Bearer {first_token}"},
        )
        rotated = client.post(
            "/sessions",
            json=_session_payload(),
            headers={"Authorization": f"Bearer {second_token}"},
        )

    assert first.status_code == 200
    assert cached.status_code == 200
    assert rotated.status_code == 401
    assert rotated.json() == {"code": "invalid_token", "retryable": False}
    assert second_token not in caplog.text


def test_configured_staging_without_principal_resolver_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = local_oidc_fixture()
    for key, value in _oidc_env(fixture).items():
        monkeypatch.setenv(key, value)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)

    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            json=_session_payload(),
            headers={"Authorization": f"Bearer {fixture.token()}"},
        )

    assert response.status_code == 503
    assert response.json() == {"code": "identity_unavailable", "retryable": False}


def test_staging_app_without_oidc_config_fails_closed_but_health_stays_secret_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "staging")
    for key in (
        "FOCUSPROOF_OIDC_ISSUER",
        "FOCUSPROOF_OIDC_AUDIENCE",
        "FOCUSPROOF_OIDC_JWKS_URI",
        "FOCUSPROOF_OIDC_ALLOWED_ALGORITHMS",
        "FOCUSPROOF_OIDC_FINGERPRINT_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    try:
        app = oidc_test_app(tmp_path, local_oidc_fixture())
    except ValidationError:
        return

    with TestClient(app) as client:
        create_response = client.post("/sessions", json=_session_payload())
        health = client.get("/health")

    assert create_response.status_code in {401, 503}
    assert "placeholder" not in health.text
    assert "FOCUSPROOF_OIDC_FINGERPRINT_KEY" not in health.text


def test_dependency_exports_exist_for_fastapi_integration() -> None:
    assert callable(get_token_verifier)
    assert callable(require_verified_identity)
