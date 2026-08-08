from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import jwt
from jwt import PyJWKSet
from openhands.sdk.event import ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
import pytest

from focusproof.api import app as app_module
from focusproof.api.app import MAX_REQUEST_BODY_BYTES, _evidence_id_for_request
from focusproof.api.models import SubmitEvidenceRequest
from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.openhands_runtime.tools.verification import VerificationObservation
from focusproof.persistence.database import create_database_engine, create_session_factory
from focusproof.persistence.security_audit import (
    SECURITY_AUDIT_RETENTION_BATCH_SIZE,
    PersistentSecurityAuditSink,
)
from focusproof.persistence.repositories import StoredSecurityAuditEvent
from focusproof.persistence.unit_of_work import UnitOfWorkFactory
from focusproof.runtime.security_audit import (
    MIN_SECURITY_AUDIT_HMAC_KEY_BYTES,
    MAX_SECURITY_AUDIT_RETENTION_SECONDS,
    SECURITY_AUDIT_OUTCOMES,
    compute_token_fingerprint,
)

from .oidc_fixture import local_oidc_fixture, oidc_test_app


_HMAC_KEY = "task5-security-audit-key-with-enough-entropy"
_OTHER_HMAC_KEY = "task5-security-audit-other-key-with-enough-entropy"
_ISSUER = "https://task5-issuer-sentinel.example.test:8443/tenant/Exact/"
_SUBJECT_A = "task5-subject-A-sentinel"
_SUBJECT_B = "task5-subject-B-sentinel"
_CLAIM_SENTINEL = "task5-private-claim-sentinel-b970ca6915f048bfa3d818e4"
_TOKEN_SENTINEL_CLAIM = "task5-token-never-log-8ed3ec49d0df45c9b61235b0"
_EVIDENCE_SENTINEL = "task5-evidence-body-secret-4bf7787ea07d44b094141ce7"
_REVIEW_SENTINEL = "task5-review-result-secret-f22ae89e78194bc49df485cc"
_JWKS_SENTINEL = "task5-jwks-private-sentinel-c9589f935ae74d6e999a"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _session_payload(title: str = "Security audit") -> dict[str, object]:
    return {
        "domain": "general",
        "title": title,
        "goal": "Prove security audit boundaries are enforced before product writes.",
    }


def _create_session(client: TestClient, token: str) -> str:
    response = client.post(
        "/sessions",
        headers=_auth(token),
        json=_session_payload(),
    )
    assert response.status_code == 200
    return str(response.json()["sessionId"])


def _disable_principal(
    app: Any,
    *,
    issuer: str,
    subject: str,
) -> None:
    with app.state.uow_factory() as uow:
        principal = uow.principals.get_exact(issuer=issuer, subject=subject)
        assert principal is not None
        assert uow.principals.set_active(principal.principal_id, active=False)
        uow.commit()


def _oversized_json_bytes() -> bytes:
    return b'{"oversized":"' + (b"x" * (MAX_REQUEST_BODY_BYTES + 1)) + b'"}'


def _install_jwks_fetch(
    monkeypatch: pytest.MonkeyPatch,
    *documents: dict[str, object] | Exception,
) -> None:
    queue = list(documents)

    def fake_fetch_data(client: jwt.PyJWKClient) -> dict[str, object]:
        document = queue.pop(0) if queue else documents[-1]
        if isinstance(document, Exception):
            raise document
        cache = getattr(client, "jwk_set_cache", None)
        if cache is not None:
            cache.put(PyJWKSet.from_dict(document))
        return document

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fake_fetch_data)


def _configure_staging_identity(
    monkeypatch: pytest.MonkeyPatch,
    fixture: Any,
    *,
    key: str = _HMAC_KEY,
    retention_seconds: str = "604800",
) -> None:
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "staging")
    monkeypatch.setenv("FOCUSPROOF_OIDC_ISSUER", fixture.issuer)
    monkeypatch.setenv("FOCUSPROOF_OIDC_AUDIENCE", fixture.audience)
    monkeypatch.setenv("FOCUSPROOF_OIDC_JWKS_URI", "https://testserver/__test__/oidc/jwks")
    monkeypatch.setenv("FOCUSPROOF_OIDC_ALLOWED_ALGORITHMS", "RS256")
    monkeypatch.setenv("FOCUSPROOF_OIDC_FINGERPRINT_KEY", key)
    monkeypatch.setenv("FOCUSPROOF_SECURITY_AUDIT_RETENTION_SECONDS", retention_seconds)


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "ai4c-identity.sqlite3"


def _migrated_uow_factory(database_path: Path) -> UnitOfWorkFactory:
    project_root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{database_path}"
    config = Config(project_root / "alembic.ini")
    config.set_main_option(
        "script_location",
        str(project_root / "agent-server/migrations"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(database_url)
    return UnitOfWorkFactory(create_session_factory(engine))


def _table_rows(database_path: Path, table: str) -> list[dict[str, object]]:
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(f"SELECT * FROM {table} ORDER BY occurred_at, id").fetchall()
    return [dict(row) for row in rows]


def _security_rows(database_path: Path) -> list[dict[str, object]]:
    return _table_rows(database_path, "security_audit_events")


def _product_database_text(database_path: Path) -> str:
    chunks: list[str] = []
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if row[0] not in {"alembic_version", "verified_principals"}
        ]
        for table in tables:
            for row in connection.execute(f"SELECT * FROM {table}").fetchall():
                chunks.append(repr(dict(row)))
    return "\n".join(chunks)


def _persistence_text(root: Path, database_path: Path) -> str:
    chunks: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path == database_path:
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
    return "\n".join(chunks)


def _facts_counts(database_path: Path) -> dict[str, int]:
    tables = (
        "learning_sessions",
        "evidence",
        "learner_answers",
        "audit_events",
        "reviews",
        "verified_principals",
        "security_audit_events",
    )
    with sqlite3.connect(database_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def _product_fact_counts(database_path: Path) -> dict[str, int]:
    tables = (
        "learning_sessions",
        "evidence",
        "learner_answers",
        "audit_events",
        "reviews",
        "verified_principals",
    )
    with sqlite3.connect(database_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def _assert_one_new_security_audit(
    database_path: Path,
    *,
    baseline_count: int,
    reason: str,
    outcome: str,
    principal_expected: bool,
) -> dict[str, object]:
    rows = _security_rows(database_path)
    assert len(rows) == baseline_count + 1
    row = rows[-1]
    assert row["request_id"]
    assert str(row["request_id"]).startswith("req_")
    assert row["reason_category"] == reason
    assert row["outcome"] == outcome
    assert (row["principal_id"] is not None) is principal_expected
    return row


def _expected_evidence_id(session_id: str) -> str:
    return _evidence_id_for_request(
        session_id,
        SubmitEvidenceRequest.model_validate(
            {
                "evidenceType": "text",
                "textContent": _EVIDENCE_SENTINEL,
            }
        ),
    )


def _verification_message(call_id: str, evidence_id: str) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="Verify repository-backed evidence")],
        tool_calls=[
            MessageToolCall(
                id=call_id,
                name="focusproof_text_evidence_verification",
                arguments=json.dumps({"evidence_id": evidence_id}),
                origin="completion",
            )
        ],
    )


def _draft_message() -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="Submit bounded review draft")],
        tool_calls=[
            MessageToolCall(
                id="call_task5_review_draft",
                name="focusproof_review_draft",
                arguments=json.dumps(
                    {
                        "credibility_findings": [_REVIEW_SENTINEL],
                        "understanding_findings": ["The explanation is specific."],
                        "contradictions": [],
                        "recommended_next_step": "Add one more source.",
                        "confidence": 0.8,
                    }
                ),
                origin="completion",
            )
        ],
    )


class Task5LlmFactory:
    def __init__(self) -> None:
        self.primary_session_id: str | None = None
        self.primary_evidence_id: str | None = None
        self._calls: Counter[str] = Counter()

    def __call__(self, session_id: str) -> TestLLM:
        self._calls[session_id] += 1
        if self.primary_session_id is None:
            self.primary_session_id = session_id
        if session_id == self.primary_session_id:
            evidence_id = _expected_evidence_id(session_id)
            self.primary_evidence_id = evidence_id
            return TestLLM.from_messages(
                [
                    _verification_message(
                        f"call_task5_verify_{self._calls[session_id]}",
                        evidence_id,
                    ),
                    _draft_message(),
                ]
            )
        assert self.primary_evidence_id is not None
        return TestLLM.from_messages(
            [
                _verification_message(
                    "call_task5_foreign_evidence",
                    self.primary_evidence_id,
                )
            ]
        )


def _verification_observations(manager: ConversationManager, session_id: str) -> list[VerificationObservation]:
    handle = manager.get(session_id)
    return [
        event.observation
        for event in handle.conversation.state.events
        if isinstance(event, ObservationEvent)
        and isinstance(event.observation, VerificationObservation)
    ]


def test_token_fingerprint_uses_hmac_only_for_present_bearer_token() -> None:
    token = b"task5.raw.token.bytes"

    first = compute_token_fingerprint(token, _HMAC_KEY)
    second = compute_token_fingerprint(token, _HMAC_KEY)
    changed_key = compute_token_fingerprint(token, _OTHER_HMAC_KEY)

    assert first == second
    assert first != changed_key
    assert first == hmac.new(_HMAC_KEY.encode("utf-8"), token, hashlib.sha256).hexdigest()
    assert first != hashlib.sha256(token).hexdigest()
    assert "task5.raw.token.bytes" not in first


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "   ",
        "too-short",
        "x" * (MIN_SECURITY_AUDIT_HMAC_KEY_BYTES - 1),
    ],
)
def test_staging_rejects_missing_blank_or_weak_security_audit_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_key: str,
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(monkeypatch, fixture, key=bad_key)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)

    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            headers=_auth(fixture.token()),
            json={
                "domain": "general",
                "title": "No weak audit key",
                "goal": "Startup must fail closed before product writes.",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"code": "identity_unavailable", "retryable": False}
    assert _facts_counts(_database_path(tmp_path)) == {
        "learning_sessions": 0,
        "evidence": 0,
        "learner_answers": 0,
        "audit_events": 0,
        "reviews": 0,
        "verified_principals": 0,
        "security_audit_events": 0,
    }


@pytest.mark.parametrize(
    "retention_seconds",
    ["", "0", "-1", str(MAX_SECURITY_AUDIT_RETENTION_SECONDS + 1), "not-a-number"],
)
def test_staging_rejects_illegal_security_audit_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retention_seconds: str,
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(
        monkeypatch,
        fixture,
        retention_seconds=retention_seconds,
    )
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)

    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            headers=_auth(fixture.token()),
            json={
                "domain": "general",
                "title": "No illegal retention",
                "goal": "Startup must fail closed before product writes.",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"code": "identity_unavailable", "retryable": False}


@pytest.mark.parametrize(
    ("headers", "reason", "fingerprint_expected"),
    [
        ({}, "missing_credentials", False),
        ({"Authorization": "Bearer"}, "missing_credentials", False),
        ({"Authorization": "Bearer not-a-jwt"}, "invalid_credentials", True),
    ],
)
def test_authentication_failures_write_exactly_one_minimized_security_audit_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    reason: str,
    fingerprint_expected: bool,
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(monkeypatch, fixture)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)

    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            headers=headers,
            json={
                "domain": "general",
                "title": "Rejected",
                "goal": "Rejected before product writes.",
            },
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"code": "invalid_token", "retryable": False}
    rows = _security_rows(_database_path(tmp_path))
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "id",
        "request_id",
        "principal_id",
        "token_fingerprint",
        "outcome",
        "reason_category",
        "occurred_at",
    }
    assert row["request_id"]
    assert row["principal_id"] is None
    assert row["outcome"] == "failure"
    assert row["reason_category"] == reason
    assert (row["token_fingerprint"] is not None) is fingerprint_expected
    assert _facts_counts(_database_path(tmp_path))["learning_sessions"] == 0


def test_security_audit_protected_route_matcher_uses_fastapi_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(monkeypatch, fixture)
    app = oidc_test_app(tmp_path, fixture)
    matcher = getattr(app_module, "_is_protected_request_scope", None)
    assert callable(matcher)

    def scope(method: str, path: str) -> dict[str, object]:
        return {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode("ascii"),
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
        }

    protected = {
        ("POST", "/sessions"),
        ("POST", "/sessions/sess_route_match/evidence"),
        ("POST", "/sessions/sess_route_match/answer"),
        ("POST", "/sessions/sess_route_match/review"),
        ("GET", "/sessions/sess_route_match"),
        ("GET", "/sessions/sess_route_match/events"),
        ("GET", "/sessions/sess_route_match/reviews"),
    }
    public = {
        ("GET", "/health"),
        ("GET", "/openhands/capabilities"),
    }

    assert sum(
        1
        for route in app.routes
        if matcher(
            app,
            scope(
                next(iter(getattr(route, "methods", {"GET"}))),
                getattr(route, "path", ""),
            ),
        )
    ) == 7
    for method, path in protected:
        assert matcher(app, scope(method, path)), (method, path)
    for method, path in public:
        assert not matcher(app, scope(method, path)), (method, path)


@pytest.mark.parametrize(
    ("path_suffix", "payload"),
    [
        ("", {}),
        ("/evidence", {}),
        ("/answer", {}),
    ],
)
def test_valid_identity_validation_errors_are_audited_once_without_product_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_suffix: str,
    payload: dict[str, object],
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(monkeypatch, fixture)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)
    token = fixture.token(subject=_SUBJECT_A)
    database_path = _database_path(tmp_path)

    with TestClient(app) as client:
        session_id = _create_session(client, token)
        baseline_security_count = len(_security_rows(database_path))
        baseline_product_counts = _product_fact_counts(database_path)
        path = "/sessions" if path_suffix == "" else f"/sessions/{session_id}{path_suffix}"
        response = client.post(
            path,
            headers={
                **_auth(token),
                "X-Request-Id": "client-spoofed-request-id",
            },
            json=payload,
        )

    assert response.status_code == 422
    assert "detail" in response.json()
    row = _assert_one_new_security_audit(
        database_path,
        baseline_count=baseline_security_count,
        reason="success",
        outcome="success",
        principal_expected=True,
    )
    assert row["request_id"] != "client-spoofed-request-id"
    assert _product_fact_counts(database_path) == baseline_product_counts


def test_valid_identity_content_length_oversize_is_audited_once_before_413(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(monkeypatch, fixture)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)
    token = fixture.token(subject=_SUBJECT_A)
    database_path = _database_path(tmp_path)

    with TestClient(app) as client:
        session_id = _create_session(client, token)
        baseline_security_count = len(_security_rows(database_path))
        baseline_product_counts = _product_fact_counts(database_path)
        response = client.post(
            f"/sessions/{session_id}/evidence",
            headers={
                **_auth(token),
                "Content-Type": "application/json",
                "X-Request-Id": "client-spoofed-request-id",
            },
            content=_oversized_json_bytes(),
        )

    assert response.status_code == 413
    assert response.json() == {"code": "request_too_large", "retryable": False}
    row = _assert_one_new_security_audit(
        database_path,
        baseline_count=baseline_security_count,
        reason="success",
        outcome="success",
        principal_expected=True,
    )
    assert row["request_id"] != "client-spoofed-request-id"
    assert _product_fact_counts(database_path) == baseline_product_counts


def test_valid_identity_chunked_oversize_is_audited_once_before_413(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(monkeypatch, fixture)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)
    token = fixture.token(subject=_SUBJECT_A)
    database_path = _database_path(tmp_path)

    def chunked_body() -> Any:
        yield b'{"oversized":"'
        yield b"x" * (MAX_REQUEST_BODY_BYTES + 1)
        yield b'"}'

    with TestClient(app) as client:
        session_id = _create_session(client, token)
        baseline_security_count = len(_security_rows(database_path))
        baseline_product_counts = _product_fact_counts(database_path)
        response = client.post(
            f"/sessions/{session_id}/answer",
            headers={**_auth(token), "Content-Type": "application/json"},
            content=chunked_body(),
        )

    assert response.status_code == 413
    assert response.json() == {"code": "request_too_large", "retryable": False}
    _assert_one_new_security_audit(
        database_path,
        baseline_count=baseline_security_count,
        reason="success",
        outcome="success",
        principal_expected=True,
    )
    assert _product_fact_counts(database_path) == baseline_product_counts


@pytest.mark.parametrize(
    ("request_kind", "bypassed_status"),
    [
        ("validation", 422),
        ("oversize", 413),
    ],
)
@pytest.mark.parametrize(
    ("credential_case", "expected_reason", "principal_expected"),
    [
        ("missing", "missing_credentials", False),
        ("invalid", "invalid_credentials", False),
        ("disabled", "forbidden", False),
    ],
)
def test_pre_handler_protected_requests_authenticate_before_validation_or_body_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request_kind: str,
    bypassed_status: int,
    credential_case: str,
    expected_reason: str,
    principal_expected: bool,
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(monkeypatch, fixture)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)
    valid_token = fixture.token(subject=_SUBJECT_A)
    database_path = _database_path(tmp_path)

    with TestClient(app) as client:
        _create_session(client, valid_token)
        if credential_case == "missing":
            headers: dict[str, str] = {}
        elif credential_case == "invalid":
            headers = {"Authorization": "Bearer invalid-token-sentinel"}
        else:
            _disable_principal(app, issuer=fixture.issuer, subject=_SUBJECT_A)
            headers = _auth(valid_token)

        baseline_security_count = len(_security_rows(database_path))
        baseline_product_counts = _product_fact_counts(database_path)
        if request_kind == "validation":
            response = client.post("/sessions", headers=headers, json={})
        else:
            response = client.post(
                "/sessions",
                headers={**headers, "Content-Type": "application/json"},
                content=_oversized_json_bytes(),
            )

    assert response.status_code != bypassed_status
    if credential_case in {"missing", "invalid"}:
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json() == {"code": "invalid_token", "retryable": False}
    else:
        assert response.status_code == 403
        assert response.json() == {"code": "forbidden", "retryable": False}
    _assert_one_new_security_audit(
        database_path,
        baseline_count=baseline_security_count,
        reason=expected_reason,
        outcome="failure",
        principal_expected=principal_expected,
    )
    assert _product_fact_counts(database_path) == baseline_product_counts


@pytest.mark.parametrize(
    "public_path",
    ["/health", "/openhands/capabilities"],
)
def test_public_routes_do_not_write_security_audit_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    public_path: str,
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(monkeypatch, fixture)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)

    with TestClient(app) as client:
        response = client.get(public_path, headers=_auth(fixture.token()))

    assert response.status_code == 200
    assert _security_rows(_database_path(tmp_path)) == []


@pytest.mark.parametrize(
    ("request_kind", "path_suffix"),
    [
        ("validation", "/answer"),
        ("oversize", "/evidence"),
    ],
)
def test_pre_handler_audit_unavailable_fails_closed_without_product_or_runtime_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request_kind: str,
    path_suffix: str,
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(monkeypatch, fixture)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)
    token = fixture.token(subject=_SUBJECT_A)
    database_path = _database_path(tmp_path)

    with TestClient(app) as client:
        session_id = _create_session(client, token)
        baseline_product_counts = _product_fact_counts(database_path)
        with app.state.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE security_audit_events")
        if request_kind == "validation":
            response = client.post(
                f"/sessions/{session_id}{path_suffix}",
                headers=_auth(token),
                json={},
            )
        else:
            response = client.post(
                f"/sessions/{session_id}{path_suffix}",
                headers={**_auth(token), "Content-Type": "application/json"},
                content=_oversized_json_bytes(),
            )

    assert response.status_code == 503
    assert response.json() == {"code": "database_unavailable", "retryable": True}
    assert _product_fact_counts(database_path) == baseline_product_counts


def test_success_forbidden_not_found_and_dependency_failures_are_audited_once_without_sensitive_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = replace(local_oidc_fixture(), issuer=_ISSUER)
    fixture.public_jwk["task5_private_jwks_sentinel"] = _JWKS_SENTINEL
    _configure_staging_identity(monkeypatch, fixture)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    llm_factory = Task5LlmFactory()
    app = oidc_test_app(tmp_path, fixture, llm_factory=llm_factory)
    token_a = fixture.token(
        subject=_SUBJECT_A,
        additional_claims={
            "private_claim": _CLAIM_SENTINEL,
            "token_sentinel": _TOKEN_SENTINEL_CLAIM,
        },
    )
    token_b = fixture.token(subject=_SUBJECT_B)
    responses: list[str] = []

    with TestClient(app) as client:
        create = client.post(
            "/sessions",
            headers=_auth(token_a),
            json={
                "domain": "general",
                "title": "Security audit",
                "goal": "Prove minimized audit records stay outside product facts.",
            },
        )
        assert create.status_code == 200
        session_id = str(create.json()["sessionId"])
        responses.append(create.text)

        evidence = client.post(
            f"/sessions/{session_id}/evidence",
            headers=_auth(token_a),
            json={"evidenceType": "text", "textContent": _EVIDENCE_SENTINEL},
        )
        review = client.post(f"/sessions/{session_id}/review", headers=_auth(token_a))
        assert evidence.status_code == 200
        assert review.status_code == 200
        responses.extend((evidence.text, review.text))

        manager: ConversationManager = app.state.conversation_manager
        observations = _verification_observations(manager, session_id)
        assert observations
        assert observations[-1].status == "success"

        state = client.get(f"/sessions/{session_id}", headers=_auth(token_a))
        events = client.get(f"/sessions/{session_id}/events", headers=_auth(token_a))
        reviews = client.get(f"/sessions/{session_id}/reviews", headers=_auth(token_a))
        assert state.status_code == events.status_code == reviews.status_code == 200
        responses.extend((state.text, events.text, reviews.text))

        owner_b = client.get(f"/sessions/{session_id}", headers=_auth(token_b))
        nonexistent = client.get("/sessions/sess_does_not_exist", headers=_auth(token_b))
        assert owner_b.status_code == nonexistent.status_code == 404
        assert owner_b.json() == nonexistent.json() == {"detail": "Session not found"}
        responses.extend((owner_b.text, nonexistent.text))

        with app.state.uow_factory() as uow:
            principal_a = uow.principals.get_exact(issuer=_ISSUER, subject=_SUBJECT_A)
            assert principal_a is not None
            assert uow.principals.set_active(principal_a.principal_id, active=False)
            uow.commit()
        disabled = client.get(f"/sessions/{session_id}", headers=_auth(token_a))
        assert disabled.status_code == 403
        assert disabled.json() == {"code": "forbidden", "retryable": False}
        responses.append(disabled.text)

    rows = _security_rows(_database_path(tmp_path))
    protected_request_count = 9
    assert len(rows) == protected_request_count
    assert {row["reason_category"] for row in rows} == {
        "success",
        "not_found",
        "forbidden",
    }
    assert sum(row["reason_category"] == "success" for row in rows) == 6
    assert sum(row["reason_category"] == "not_found" for row in rows) == 2
    assert sum(row["reason_category"] == "forbidden" for row in rows) == 1
    assert len({row["request_id"] for row in rows}) == protected_request_count
    assert all(str(row["request_id"]).startswith("req_") for row in rows)
    assert all(row["token_fingerprint"] for row in rows)
    assert all(row["outcome"] in SECURITY_AUDIT_OUTCOMES for row in rows)

    fingerprint = hmac.new(
        _HMAC_KEY.encode("utf-8"),
        token_a.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    security_dump = "\n".join(repr(row) for row in rows)
    assert fingerprint in security_dump
    assert f"hmac-sha256:{fingerprint}" not in security_dump
    assert principal_a.principal_id in security_dump
    scanned = "\n".join(
        (
            security_dump,
            *responses,
            caplog.text,
            _product_database_text(_database_path(tmp_path)),
            _persistence_text(tmp_path, _database_path(tmp_path)),
        )
    )
    for forbidden in (
        token_a,
        token_b,
        _JWKS_SENTINEL,
        _CLAIM_SENTINEL,
        _TOKEN_SENTINEL_CLAIM,
        _ISSUER,
        _SUBJECT_A,
        _SUBJECT_B,
        _HMAC_KEY,
    ):
        assert forbidden not in scanned
    assert _EVIDENCE_SENTINEL not in security_dump
    assert _REVIEW_SENTINEL not in security_dump
    assert _EVIDENCE_SENTINEL not in caplog.text
    assert _REVIEW_SENTINEL not in caplog.text


def test_security_audit_unavailable_fails_closed_before_product_or_runtime_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = local_oidc_fixture()
    _configure_staging_identity(monkeypatch, fixture)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture)

    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            headers=_auth(fixture.token()),
            json={
                "domain": "general",
                "title": "Audit outage",
                "goal": "No product write without audit.",
            },
        )
        assert response.status_code == 200
        session_id = str(response.json()["sessionId"])
        baseline = _product_fact_counts(_database_path(tmp_path))
        with app.state.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE security_audit_events")
        denied = client.post(
            f"/sessions/{session_id}/evidence",
            headers=_auth(fixture.token()),
            json={
                "evidenceType": "text",
                "textContent": "This must not be written.",
            },
        )

    assert denied.status_code == 503
    assert denied.json() == {"code": "database_unavailable", "retryable": True}
    with sqlite3.connect(_database_path(tmp_path)) as connection:
        after = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in baseline
        }
    assert after == baseline


def test_retention_deletes_only_expired_rows_in_bounded_batches_and_survives_restart(
    tmp_path: Path,
) -> None:
    database_path = _database_path(tmp_path)
    uow_factory = _migrated_uow_factory(database_path)
    sink = PersistentSecurityAuditSink(
        uow_factory,
        retention_seconds=60,
    )
    old_time = datetime.now(UTC) - timedelta(seconds=120)
    boundary_time = datetime.now(UTC) - timedelta(seconds=60)
    current_time = datetime.now(UTC)
    with uow_factory() as uow:
        for index in range(SECURITY_AUDIT_RETENTION_BATCH_SIZE + 3):
            uow.security_audit.add(
                StoredSecurityAuditEvent(
                    id=f"audit_old_{index:02d}",
                    request_id=f"req_old_{index:02d}",
                    principal_id=None,
                    token_fingerprint=None,
                    outcome="failure",
                    reason_category="invalid_credentials",
                    occurred_at=old_time,
                )
            )
        uow.security_audit.add(
            StoredSecurityAuditEvent(
                id="audit_boundary",
                request_id="req_boundary",
                principal_id=None,
                token_fingerprint=None,
                outcome="failure",
                reason_category="invalid_credentials",
                occurred_at=boundary_time,
            )
        )
        uow.security_audit.add(
            StoredSecurityAuditEvent(
                id="audit_current",
                request_id="req_current",
                principal_id=None,
                token_fingerprint=None,
                outcome="failure",
                reason_category="invalid_credentials",
                occurred_at=current_time,
            )
        )
        uow.commit()

    sink.sweep_expired(now=boundary_time + timedelta(seconds=60))
    rows_after_first_sweep = _security_rows(database_path)
    assert len([row for row in rows_after_first_sweep if str(row["id"]).startswith("audit_old")]) == 3
    assert any(row["id"] == "audit_boundary" for row in rows_after_first_sweep)
    assert any(row["id"] == "audit_current" for row in rows_after_first_sweep)

    restarted_sink = PersistentSecurityAuditSink(
        _migrated_uow_factory(database_path),
        retention_seconds=60,
    )
    restarted_sink.sweep_expired(now=boundary_time + timedelta(seconds=60))
    rows_after_restart = _security_rows(database_path)
    assert {row["id"] for row in rows_after_restart} == {
        "audit_boundary",
        "audit_current",
    }


def test_concurrent_security_audit_records_are_complete_and_unique(
    tmp_path: Path,
) -> None:
    database_path = _database_path(tmp_path)
    sink = PersistentSecurityAuditSink(
        _migrated_uow_factory(database_path),
        retention_seconds=604800,
    )

    def request_once(index: int) -> None:
        sink.record(
            principal_id=f"principal_concurrent_{index}",
            request_id=f"req_concurrent_{index}",
            token_fingerprint=compute_token_fingerprint(
                f"token-{index}".encode("utf-8"),
                _HMAC_KEY,
            ),
            outcome="success",
            reason_category="success",
            occurred_at=datetime.now(UTC),
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(request_once, range(8)))

    rows = _security_rows(database_path)
    assert len(rows) == 8
    assert len({row["request_id"] for row in rows}) == 8
    assert all(row["principal_id"] for row in rows)
    assert all(row["token_fingerprint"] for row in rows)
