from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
from typing import Any, cast

from fastapi.testclient import TestClient
import jwt
from jwt import PyJWKSet
from openhands.sdk.event import ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
import pytest

from focusproof.api.app import _evidence_id_for_request
from focusproof.api.models import SubmitEvidenceRequest
from focusproof.openhands_runtime.manager import ConversationManager
from focusproof.openhands_runtime.tools.verification import VerificationObservation

from .oidc_fixture import local_oidc_fixture, oidc_test_app


_FINGERPRINT_KEY = "repair1-fingerprint-key"
_ISSUER = "https://issuer-sentinel.example.test:8443/tenant/Exact/"
_SUBJECT_A = "subject-A-sentinel"
_SUBJECT_B = "subject-B-sentinel"
_CLAIM_SENTINEL = "private-claim-sentinel"
_EVIDENCE_PAYLOAD = {
    "evidenceType": "text",
    "textContent": (
        "A concrete example shows that the server resolves ownership before "
        "repository access and keeps the verified binding outside model arguments."
    ),
}


def _verification_message(
    *,
    call_id: str,
    evidence_id: str,
) -> Message:
    verification = MessageToolCall(
        id=call_id,
        name="focusproof_text_evidence_verification",
        arguments=json.dumps({"evidence_id": evidence_id}),
        origin="completion",
    )
    return Message(
        role="assistant",
        content=[TextContent(text="Verify repository-backed evidence")],
        tool_calls=[verification],
    )


def _draft_message() -> Message:
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
    return Message(
        role="assistant",
        content=[TextContent(text="Submit bounded review draft")],
        tool_calls=[draft],
    )


def _expected_evidence_id(session_id: str) -> str:
    return _evidence_id_for_request(
        session_id,
        SubmitEvidenceRequest.model_validate(_EVIDENCE_PAYLOAD),
    )


class RepositoryToolTestLlmFactory:
    def __init__(self) -> None:
        self.primary_session_id: str | None = None
        self.primary_evidence_id: str | None = None
        self._creation_counts: dict[str, int] = {}

    def __call__(self, session_id: str) -> TestLLM:
        creation = self._creation_counts.get(session_id, 0) + 1
        self._creation_counts[session_id] = creation
        if self.primary_session_id is None:
            self.primary_session_id = session_id
        if session_id == self.primary_session_id:
            evidence_id = _expected_evidence_id(session_id)
            messages: list[Message | Exception] = [
                _verification_message(
                    call_id=f"call_owner_a_verify_{creation}",
                    evidence_id=evidence_id,
                )
            ]
            if creation > 1:
                messages.append(_draft_message())
            return TestLLM.from_messages(messages)

        assert self.primary_evidence_id is not None
        return TestLLM.from_messages(
            [
                _verification_message(
                    call_id="call_owner_b_foreign_evidence",
                    evidence_id=self.primary_evidence_id,
                ),
                _verification_message(
                    call_id="call_owner_b_missing_evidence",
                    evidence_id="ev_does_not_exist",
                ),
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


def _verification_observations(handle: Any) -> list[VerificationObservation]:
    return [
        event.observation
        for event in handle.conversation.state.events
        if isinstance(event, ObservationEvent)
        and isinstance(event.observation, VerificationObservation)
    ]


def _scoped_repository(handle: Any) -> object:
    tool = handle.conversation.agent.tools_map[
        "focusproof_text_evidence_verification"
    ]
    return cast(Any, tool.executor)._repository


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
    llm_factory = RepositoryToolTestLlmFactory()
    app = oidc_test_app(tmp_path, fixture, llm_factory=llm_factory)
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
        assert llm_factory.primary_session_id == session_id

        evidence = client.post(
            f"/sessions/{session_id}/evidence",
            headers=_authorization(token_a),
            json=_EVIDENCE_PAYLOAD,
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
        evidence_id = str(evidence.json()["evidenceId"])
        assert evidence_id == _expected_evidence_id(session_id)
        llm_factory.primary_evidence_id = evidence_id
        responses.extend((evidence.text, answer.text))

        with app.state.uow_factory() as uow:
            principal = uow.principals.get_exact(
                issuer=_ISSUER,
                subject=_SUBJECT_A,
            )
        assert principal is not None
        manager: ConversationManager = app.state.conversation_manager

        warm_review = client.post(
            f"/sessions/{session_id}/review",
            headers=_authorization(token_a),
        )
        assert warm_review.status_code == 503
        responses.append(warm_review.text)
        warm_handle = manager.get(session_id)
        warm_observations = _verification_observations(warm_handle)
        assert len(warm_observations) == 1
        warm_observation = warm_observations[0]
        assert warm_observation.status == "success"
        assert warm_observation.evidence_id == evidence_id
        assert set(warm_observation.facts) == {
            "has_text",
            "character_count",
            "word_count",
            "has_concrete_example",
            "has_structured_output",
            "content_hash",
        }
        assert warm_observation.source_refs == [
            evidence_id,
            warm_observation.facts["content_hash"],
        ]
        assert _EVIDENCE_PAYLOAD["textContent"] not in warm_observation.model_dump_json()
        warm_repository = _scoped_repository(warm_handle)

        before_principal_b = _database_counts(database_path)
        establish_principal_b = client.get(
            "/sessions/sess_does_not_exist",
            headers=_authorization(token_b),
        )
        assert establish_principal_b.status_code == 404
        with app.state.uow_factory() as uow:
            principal_b = uow.principals.get_exact(
                issuer=_ISSUER,
                subject=_SUBJECT_B,
            )
        assert principal_b is not None
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

        native_before_denied_restore = len(warm_handle.conversation.state.events)
        denial_baseline = _database_counts(database_path)
        with pytest.raises(PermissionError):
            manager.get_or_restore(session_id, principal_b.principal_id)
        assert _database_counts(database_path) == denial_baseline
        assert len(warm_handle.conversation.state.events) == native_before_denied_restore

        manager.close(session_id, principal.principal_id)
        base_state_path = (
            warm_handle.persistence_path
            / warm_handle.conversation_id.hex
            / "base_state.json"
        )
        base_state_text = base_state_path.read_text(encoding="utf-8")
        base_state = json.loads(base_state_text)
        verifier_params = [
            tool["params"]
            for tool in base_state["agent"]["tools"]
            if tool["name"] == "FocusProofTextEvidenceVerificationTool"
        ]
        assert verifier_params
        assert all(params["session_id"] == session_id for params in verifier_params)
        assert all(params["repository"] == {} for params in verifier_params)

        cold_denial_baseline = _database_counts(database_path)
        with pytest.raises(PermissionError):
            manager.get_or_restore(session_id, principal_b.principal_id)
        assert _database_counts(database_path) == cold_denial_baseline

        reviewed = client.post(
            f"/sessions/{session_id}/review",
            headers=_authorization(token_a),
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["reviewStatus"] == "completed"
        responses.append(reviewed.text)
        restored_handle = manager.get(session_id)
        assert restored_handle.compatibility_restore is True
        assert restored_handle.conversation.agent is restored_handle.conversation.state.agent
        assert _scoped_repository(restored_handle) is not warm_repository
        restored_observations = _verification_observations(restored_handle)
        assert len(restored_observations) >= 2
        assert restored_observations[-1].status == "success"
        assert restored_observations[-1].evidence_id == evidence_id

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

        baseline = _database_counts(database_path)
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

        owner_b_session = client.post(
            "/sessions",
            headers=_authorization(token_b),
            json={
                "domain": "general",
                "title": "Foreign evidence boundary",
                "goal": "Prove model actions cannot cross the owner boundary.",
                "expectedOutput": "Two safe not-found observations",
                "plannedMinutes": 10,
            },
        )
        assert owner_b_session.status_code == 200
        owner_b_session_id = str(owner_b_session.json()["sessionId"])
        owner_b_review = client.post(
            f"/sessions/{owner_b_session_id}/review",
            headers=_authorization(token_b),
        )
        assert owner_b_review.status_code == 503
        owner_b_handle = manager.get(owner_b_session_id)
        owner_b_observations = _verification_observations(owner_b_handle)
        assert len(owner_b_observations) == 2
        assert [item.status for item in owner_b_observations] == ["failed", "failed"]
        assert [item.error_code for item in owner_b_observations] == [
            "evidence_not_found",
            "evidence_not_found",
        ]
        assert owner_b_observations[0].facts == owner_b_observations[1].facts == {}
        assert (
            owner_b_observations[0].safe_error_message
            == owner_b_observations[1].safe_error_message
            == "Evidence was not found."
        )
        owner_b_observation_dump = "\n".join(
            item.model_dump_json() for item in owner_b_observations
        )
        assert _EVIDENCE_PAYLOAD["textContent"] not in owner_b_observation_dump
        assert warm_observation.facts["content_hash"] not in owner_b_observation_dump
        responses.extend((owner_b_session.text, owner_b_review.text))

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
                base_state_text,
            )
        )
        for private_binding in ("_principal_id", "_uow_factory", "_repository"):
            assert private_binding not in runtime_dump
        assert principal.principal_id not in runtime_dump
        assert principal_b.principal_id not in runtime_dump

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
