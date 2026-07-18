from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import jwt
from jwt import PyJWKSet
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
import pytest

from focusproof.openhands_runtime.manager import ConversationManager

from .oidc_fixture import local_oidc_fixture, oidc_test_app


_FINGERPRINT_KEY = "repair1-fingerprint-key"
_ISSUER = "https://issuer-sentinel.example.test:8443/tenant/Exact/"
_SUBJECT_A = "subject-A-sentinel"
_SUBJECT_B = "subject-B-sentinel"
_CLAIM_SENTINEL = "private-claim-sentinel"


def _review_llm(_: str) -> TestLLM:
    draft = MessageToolCall(
        id="call_repair1_review_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Repository evidence is attributable."],
                "understanding_findings": ["The explanation is specific."],
                "contradictions": [],
                "recommended_next_step": "Compare one additional source.",
                "confidence": 0.8,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Submit bounded review draft")],
                tool_calls=[draft],
            )
        ]
    )


def _install_jwks_fetch(
    monkeypatch: pytest.MonkeyPatch,
    document: dict[str, object],
) -> None:
    def fake_fetch_data(client: jwt.PyJWKClient) -> dict[str, object]:
        cache = getattr(client, "jwk_set_cache", None)
        if cache is not None:
            cache.put(PyJWKSet.from_dict(document))
        return document

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fake_fetch_data)


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _database_counts(database_path: Path) -> dict[str, int]:
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
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            chunks.extend(repr(dict(row)) for row in rows)
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


def _request_matrix() -> tuple[tuple[str, str, dict[str, object] | None], ...]:
    return (
        ("GET", "", None),
        ("GET", "/events", None),
        ("GET", "/reviews", None),
        (
            "POST",
            "/evidence",
            {"evidenceType": "text", "textContent": "Owner B must not write."},
        ),
        (
            "POST",
            "/answer",
            {"questionId": "q-denied", "answer": "Owner B must not write."},
        ),
        ("POST", "/review", None),
    )


def test_real_signed_identity_chain_is_owner_isolated_and_identity_material_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = replace(local_oidc_fixture(), issuer=_ISSUER)
    database_path = tmp_path / "ai4c-identity.sqlite3"
    monkeypatch.setenv("FOCUSPROOF_PROFILE", "staging")
    monkeypatch.setenv("FOCUSPROOF_OIDC_ISSUER", fixture.issuer)
    monkeypatch.setenv("FOCUSPROOF_OIDC_AUDIENCE", fixture.audience)
    monkeypatch.setenv(
        "FOCUSPROOF_OIDC_JWKS_URI",
        "https://testserver/__test__/oidc/jwks",
    )
    monkeypatch.setenv("FOCUSPROOF_OIDC_ALLOWED_ALGORITHMS", "RS256")
    monkeypatch.setenv("FOCUSPROOF_OIDC_FINGERPRINT_KEY", _FINGERPRINT_KEY)
    _install_jwks_fetch(monkeypatch, {"keys": [fixture.public_jwk]})
    app = oidc_test_app(tmp_path, fixture, llm_factory=_review_llm)
    token_a = fixture.token(
        subject=_SUBJECT_A,
        additional_claims={"private_claim": _CLAIM_SENTINEL},
    )
    token_b = fixture.token(subject=_SUBJECT_B)
    responses: list[str] = []

    with TestClient(app) as client:
        created = client.post(
            "/sessions",
            headers=_authorization(token_a),
            json={
                "domain": "general",
                "title": "Identity-isolated review",
                "goal": "Explain why server-bound identity prevents spoofing.",
                "expectedOutput": "A bounded explanation",
                "plannedMinutes": 20,
            },
        )
        assert created.status_code == 200
        responses.append(created.text)
        session_id = str(created.json()["sessionId"])

        evidence = client.post(
            f"/sessions/{session_id}/evidence",
            headers=_authorization(token_a),
            json={
                "evidenceType": "text",
                "textContent": "The server resolves ownership before any mutation.",
            },
        )
        answer = client.post(
            f"/sessions/{session_id}/answer",
            headers=_authorization(token_a),
            json={
                "questionId": "q-owner-boundary",
                "answer": "The bearer token is verified before repository access.",
            },
        )
        assert evidence.status_code == 200
        assert answer.status_code == 200
        responses.extend((evidence.text, answer.text))

        with app.state.uow_factory() as uow:
            principal = uow.principals.get_exact(
                issuer=_ISSUER,
                subject=_SUBJECT_A,
            )
        assert principal is not None
        manager: ConversationManager = app.state.conversation_manager
        manager.close(session_id, principal.principal_id)

        reviewed = client.post(
            f"/sessions/{session_id}/review",
            headers=_authorization(token_a),
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["reviewStatus"] == "completed"
        responses.append(reviewed.text)

        state = client.get(
            f"/sessions/{session_id}", headers=_authorization(token_a)
        )
        events = client.get(
            f"/sessions/{session_id}/events", headers=_authorization(token_a)
        )
        reviews = client.get(
            f"/sessions/{session_id}/reviews", headers=_authorization(token_a)
        )
        assert len(state.json()["state"]["evidence"]) == 1
        assert len(state.json()["state"]["answers"]) == 1
        assert len(events.json()["events"]) > 0
        assert len(reviews.json()["reviews"]) == 1
        responses.extend((state.text, events.text, reviews.text))

        before_principal_b = _database_counts(database_path)
        establish_principal_b = client.get(
            "/sessions/sess_does_not_exist",
            headers=_authorization(token_b),
        )
        assert establish_principal_b.status_code == 404
        after_principal_b = _database_counts(database_path)
        assert after_principal_b["verified_principals"] == 2
        assert {
            key: value
            for key, value in after_principal_b.items()
            if key != "verified_principals"
        } == {
            key: value
            for key, value in before_principal_b.items()
            if key != "verified_principals"
        }
        responses.append(establish_principal_b.text)
        baseline = after_principal_b
        assert all(value > 0 for value in baseline.values())
        for method, suffix, payload in _request_matrix():
            denied = client.request(
                method,
                f"/sessions/{session_id}{suffix}",
                headers=_authorization(token_b),
                json=payload,
            )
            nonexistent = client.request(
                method,
                f"/sessions/sess_does_not_exist{suffix}",
                headers=_authorization(token_b),
                json=payload,
            )
            assert denied.status_code == nonexistent.status_code == 404
            assert denied.json() == nonexistent.json() == {"detail": "Session not found"}
            assert _database_counts(database_path) == baseline
            responses.extend((denied.text, nonexistent.text))

        missing = client.get(f"/sessions/{session_id}")
        invalid = client.get(
            f"/sessions/{session_id}", headers=_authorization("not-a-jwt")
        )
        assert missing.status_code == invalid.status_code == 401
        assert _database_counts(database_path) == baseline
        responses.extend((missing.text, invalid.text))

        with app.state.uow_factory() as uow:
            assert uow.principals.set_active(principal.principal_id, active=False)
            uow.commit()
        disabled_baseline = _database_counts(database_path)
        disabled = client.get(
            f"/sessions/{session_id}", headers=_authorization(token_a)
        )
        assert disabled.status_code == 403
        assert disabled.json() == {"code": "forbidden", "retryable": False}
        assert _database_counts(database_path) == disabled_baseline
        responses.append(disabled.text)

        handle = manager.get(session_id)
        runtime_dump = "\n".join(
            (
                handle.conversation.agent.model_dump_json(),
                handle.conversation.state.model_dump_json(),
            )
        )

    fingerprint = "hmac-sha256:" + hmac.new(
        _FINGERPRINT_KEY.encode(), token_a.encode(), hashlib.sha256
    ).hexdigest()
    scanned = "\n".join(
        (
            *responses,
            caplog.text,
            _product_database_text(database_path),
            _persistence_text(tmp_path, database_path),
            runtime_dump,
        )
    )
    for forbidden in (
        token_a,
        token_b,
        _CLAIM_SENTINEL,
        _ISSUER,
        _SUBJECT_A,
        _SUBJECT_B,
        fingerprint,
    ):
        assert forbidden not in scanned
