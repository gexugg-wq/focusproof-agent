from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi import Request
from fastapi.testclient import TestClient
import jwt
from jwt import PyJWKSet
from pydantic import ValidationError
import pytest
from sqlalchemy.exc import OperationalError

from focusproof.api.auth import DEVELOPMENT_USER_ID, VerifiedIdentity, get_verified_identity
from focusproof.api.oidc import (
    IdentityUnavailableError,
    InvalidTokenError,
    OidcTokenVerifier,
    get_token_verifier,
    require_verified_identity,
)
from focusproof.config.identity import OidcSettings, load_oidc_settings
from focusproof.persistence.providers import PrincipalDisabledError

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


def _identity_fact_counts(database_path: Path) -> dict[str, int]:
    tables = (
        "verified_principals",
        "learning_sessions",
        "evidence",
        "learner_answers",
        "audit_events",
        "reviews",
    )
    with sqlite3.connect(database_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def _runtime_artifacts(root: Path, database_path: Path) -> set[str]:
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path != database_path
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
        "FOCUSPROOF_OIDC_JWKS_URI": "https://testserver/__test__/oidc/jwks",
        "FOCUSPROOF_OIDC_ALLOWED_ALGORITHMS": "RS256",
        "FOCUSPROOF_OIDC_FINGERPRINT_KEY": (
            "identity-authorization-test-hmac-key-32"
        ),
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


class MutableMonotonicClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class MutableJwksSource:
    def __init__(self, document: dict[str, object]) -> None:
        self._document = document
        self._failure: Exception | None = None
        self.fetch_count = 0

    def set_document(self, document: dict[str, object]) -> None:
        self._document = document
        self._failure = None

    def fail_with(self, exc: Exception) -> None:
        self._failure = exc

    def fetch_data(self, self_client: jwt.PyJWKClient) -> dict[str, object]:
        del self_client
        self.fetch_count += 1
        if self._failure is not None:
            raise self._failure
        return self._document


def _install_dynamic_jwks_fetch(
    monkeypatch: pytest.MonkeyPatch,
    source: MutableJwksSource,
) -> None:
    def fake_fetch_data(self: jwt.PyJWKClient) -> dict[str, object]:
        return source.fetch_data(self)

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fake_fetch_data)


def _settings_for_fixture(fixture: LocalOidcFixture) -> OidcSettings:
    return load_oidc_settings(_oidc_env(fixture), profile="staging")


def _request_with_anonymous_mode(*, allow_anonymous_identity: bool) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "root_path": "",
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    allow_anonymous_identity=allow_anonymous_identity,
                )
            ),
        }
    )


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


@pytest.mark.parametrize("profile", ["staging", "production"])
@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("FOCUSPROOF_OIDC_ISSUER", "http://issuer.example.test/realm"),
        ("FOCUSPROOF_OIDC_JWKS_URI", "http://jwks.example.test/keys"),
        ("FOCUSPROOF_OIDC_ISSUER", "https://user@issuer.example.test/realm"),
        ("FOCUSPROOF_OIDC_JWKS_URI", "https://user:pass@jwks.example.test/keys"),
        ("FOCUSPROOF_OIDC_ISSUER", "https://issuer.example.test/realm#fragment"),
        ("FOCUSPROOF_OIDC_JWKS_URI", "https://jwks.example.test/keys#fragment"),
        ("FOCUSPROOF_OIDC_ISSUER", "https://issuer.example.test/realm?tenant=a"),
        ("FOCUSPROOF_OIDC_JWKS_URI", "https://jwks.example.test/keys?version=1"),
        ("FOCUSPROOF_OIDC_ISSUER", " https://issuer.example.test/realm"),
        ("FOCUSPROOF_OIDC_JWKS_URI", "https://jwks.example.test/keys "),
    ],
)
def test_non_local_profiles_reject_unsafe_or_non_exact_oidc_urls(
    profile: str,
    field: str,
    unsafe_value: str,
) -> None:
    fixture = local_oidc_fixture()
    environ = _oidc_env(fixture, profile=profile)
    environ[field] = unsafe_value

    with pytest.raises(ValidationError):
        load_oidc_settings(environ, profile=profile)


@pytest.mark.parametrize("profile", ["staging", "production"])
def test_non_local_profiles_preserve_fixed_https_oidc_urls_exactly(
    profile: str,
) -> None:
    fixture = local_oidc_fixture()
    environ = _oidc_env(fixture, profile=profile)
    issuer = "https://Issuer.Example.test:8443/Realm/"
    jwks_uri = "https://JWKS.Example.test:9443/keys/v1"
    environ["FOCUSPROOF_OIDC_ISSUER"] = issuer
    environ["FOCUSPROOF_OIDC_JWKS_URI"] = jwks_uri

    settings = load_oidc_settings(environ, profile=profile)

    assert settings.issuer == issuer
    assert settings.jwks_uri == jwks_uri


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


def test_verifier_accepts_standard_signed_token_without_optional_nbf_and_rejects_future_nbf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    fixture = local_oidc_fixture()
    resolver = StaticPrincipalResolver("principal_oidc_optional_nbf")
    for key, value in _oidc_env(fixture).items():
        monkeypatch.setenv(key, value)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(
        tmp_path,
        fixture,
        principal_resolver=resolver,
    )
    now = int(time.time())
    token_without_nbf = jwt.encode(
        {
            "iss": fixture.issuer,
            "aud": fixture.audience,
            "sub": "keycloak-style-subject",
            "iat": now,
            "exp": now + 300,
        },
        fixture.private_key_pem,
        algorithm="RS256",
        headers={"kid": fixture.kid},
    )
    future_nbf = fixture.token(not_before_delta_seconds=300)

    with TestClient(app) as client:
        accepted = client.post(
            "/sessions",
            json=_session_payload(),
            headers={"Authorization": f"Bearer {token_without_nbf}"},
        )
        rejected = client.post(
            "/sessions",
            json=_session_payload(),
            headers={"Authorization": f"Bearer {future_nbf}"},
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 401
    assert rejected.json() == {"code": "invalid_token", "retryable": False}


@pytest.mark.parametrize("subject", [" subject", "subject "])
def test_signed_subject_with_boundary_whitespace_is_invalid_before_any_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject: str,
) -> None:
    fixture = local_oidc_fixture()
    for key, value in _oidc_env(fixture).items():
        monkeypatch.setenv(key, value)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)
    database_path = tmp_path / "ai4c-identity.sqlite3"

    with TestClient(app, raise_server_exceptions=False) as client:
        facts_before = _identity_fact_counts(database_path)
        runtime_before = _runtime_artifacts(tmp_path, database_path)
        response = client.post(
            "/sessions",
            json=_session_payload(),
            headers={"Authorization": f"Bearer {fixture.token(subject=subject)}"},
        )
        facts_after = _identity_fact_counts(database_path)
        runtime_after = _runtime_artifacts(tmp_path, database_path)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"code": "invalid_token", "retryable": False}
    assert facts_before == facts_after == {table: 0 for table in facts_before}
    assert runtime_before == runtime_after


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


def test_configured_staging_uses_database_principal_resolver_when_not_injected(
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

    assert response.status_code == 200
    session_id = str(response.json()["sessionId"])
    with TestClient(app) as client:
        state = client.get(
            f"/sessions/{session_id}",
            headers={"Authorization": f"Bearer {fixture.token()}"},
        )
    assert state.status_code == 200
    assert state.json()["state"]["ownerUserId"].startswith("principal_")


def test_disabled_principal_is_forbidden_before_resource_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = local_oidc_fixture()
    for key, value in _oidc_env(fixture).items():
        monkeypatch.setenv(key, value)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})

    class DisabledResolver:
        def resolve(self, *, issuer: str, subject: str) -> str:
            del issuer, subject
            raise PrincipalDisabledError()

    app = oidc_test_app(tmp_path, fixture, principal_resolver=DisabledResolver())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/sessions/sess_does_not_exist",
            headers={"Authorization": f"Bearer {fixture.token()}"},
        )

    assert response.status_code == 403
    assert response.json() == {"code": "forbidden", "retryable": False}


def test_principal_database_outage_remains_database_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = local_oidc_fixture()
    for key, value in _oidc_env(fixture).items():
        monkeypatch.setenv(key, value)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})

    class UnavailableResolver:
        def resolve(self, *, issuer: str, subject: str) -> str:
            del issuer, subject
            raise OperationalError("resolve principal", {}, RuntimeError("db down"))

    app = oidc_test_app(tmp_path, fixture, principal_resolver=UnavailableResolver())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/sessions",
            json=_session_payload(),
            headers={"Authorization": f"Bearer {fixture.token()}"},
        )

    assert response.status_code == 503
    assert response.json() == {"code": "database_unavailable", "retryable": True}


def test_verifier_uses_cached_signing_key_until_ttl_then_fails_closed_on_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = local_oidc_fixture()
    clock = MutableMonotonicClock()
    source = MutableJwksSource({"keys": [fixture.public_jwk]})
    _install_dynamic_jwks_fetch(monkeypatch, source)
    verifier = OidcTokenVerifier(
        _settings_for_fixture(fixture),
        principal_resolver=StaticPrincipalResolver("principal_oidc_ttl"),
        monotonic_clock=clock,
        max_cached_signing_keys=2,
    )
    token = fixture.token()

    first = asyncio.run(verifier.verify(token))
    source.fail_with(RuntimeError("jwks offline"))
    cached = asyncio.run(verifier.verify(token))
    clock.advance(301)

    assert first.principal_id == "principal_oidc_ttl"
    assert cached.principal_id == "principal_oidc_ttl"
    assert source.fetch_count == 1
    with pytest.raises(InvalidTokenError):
        asyncio.run(verifier.verify(token))
    assert source.fetch_count == 2


def test_verifier_refreshes_same_kid_after_ttl_and_rejects_old_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_fixture = local_oidc_fixture()
    second_fixture = local_oidc_fixture()
    clock = MutableMonotonicClock()
    source = MutableJwksSource({"keys": [first_fixture.public_jwk]})
    _install_dynamic_jwks_fetch(monkeypatch, source)
    verifier = OidcTokenVerifier(
        _settings_for_fixture(first_fixture),
        principal_resolver=StaticPrincipalResolver("principal_oidc_rotation"),
        monotonic_clock=clock,
        max_cached_signing_keys=2,
    )
    rotated_jwk = dict(second_fixture.public_jwk, kid=first_fixture.kid)
    old_token = first_fixture.token()

    first = asyncio.run(verifier.verify(old_token))
    clock.advance(301)
    source.set_document({"keys": [rotated_jwk]})
    new_token = second_fixture.token(kid=first_fixture.kid)
    refreshed = asyncio.run(verifier.verify(new_token))

    assert first.principal_id == "principal_oidc_rotation"
    assert refreshed.principal_id == "principal_oidc_rotation"
    with pytest.raises(InvalidTokenError):
        asyncio.run(verifier.verify(old_token))


def test_verifier_evicts_oldest_kid_when_cache_capacity_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = [local_oidc_fixture(), local_oidc_fixture(), local_oidc_fixture()]
    clock = MutableMonotonicClock()
    source = MutableJwksSource({"keys": [fixtures[0].public_jwk]})
    _install_dynamic_jwks_fetch(monkeypatch, source)
    verifier = OidcTokenVerifier(
        _settings_for_fixture(fixtures[0]),
        principal_resolver=StaticPrincipalResolver("principal_oidc_capacity"),
        monotonic_clock=clock,
        max_cached_signing_keys=2,
    )

    for fixture in fixtures:
        source.set_document({"keys": [fixture.public_jwk]})
        identity = asyncio.run(verifier.verify(fixture.token()))
        assert identity.principal_id == "principal_oidc_capacity"

    source.fail_with(RuntimeError("jwks offline"))
    cached_second = asyncio.run(verifier.verify(fixtures[1].token()))
    cached_third = asyncio.run(verifier.verify(fixtures[2].token()))

    assert cached_second.principal_id == "principal_oidc_capacity"
    assert cached_third.principal_id == "principal_oidc_capacity"
    with pytest.raises(InvalidTokenError):
        asyncio.run(verifier.verify(fixtures[0].token()))


def test_identity_dependency_is_async_and_only_allows_local_dev_anonymous() -> None:
    assert inspect.iscoroutinefunction(get_verified_identity)

    local_dev_identity = asyncio.run(
        get_verified_identity(
            _request_with_anonymous_mode(allow_anonymous_identity=True),
            None,
        )
    )

    assert local_dev_identity.principal_id == DEVELOPMENT_USER_ID

    with pytest.raises(IdentityUnavailableError):
        asyncio.run(
            get_verified_identity(
                _request_with_anonymous_mode(allow_anonymous_identity=False),
                None,
            )
        )


def test_explicit_deterministic_profile_does_not_enable_anonymous_business_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = local_oidc_fixture()
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "deterministic-test")
    app = oidc_test_app(tmp_path, fixture)

    with TestClient(app) as client:
        response = client.post("/sessions", json=_session_payload())
        health = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"code": "identity_unavailable", "retryable": False}
    assert health.json()["readiness"] == "identity_unavailable"


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
