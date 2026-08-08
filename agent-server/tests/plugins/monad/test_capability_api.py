from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Self

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import ToolDefinition, ToolExecutor
from pydantic import BaseModel, ConfigDict, PrivateAttr, model_serializer

from focusproof.api import app as app_module
from focusproof.api.models import SubmitEvidenceRequest
from focusproof.domain.plugins.base import PublicPluginCapability
from focusproof.domain.plugins.monad.models import (
    MonadEvidence,
    MonadVerificationObservation as MonadVerifierResult,
)
from focusproof.domain.plugins.monad.tool import MonadVerificationTool
from focusproof.openhands_runtime.capabilities import VerificationCapability
from focusproof.openhands_runtime.tools import (
    EvidenceReferenceAction,
    SessionEvidenceRepository,
    VerificationObservation,
    read_only_annotations,
    utc_now,
)
from focusproof.runtime.evidence import Evidence


CONTRACT = "0x52908400098527886E0F7030069857D2E4169EE7"
WALLET = "0xde709f2102306220921060314715629080e2fb77"
TX_HASH = "0x" + "ab" * 32


def _migrated_app(
    tmp_path: Path,
    *,
    llm_factory: Any,
    monkeypatch: Any,
    env: dict[str, str] | None = None,
) -> TestClient:
    project_root = Path(__file__).resolve().parents[4]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'capability-api.sqlite3'}"
    config = Config(project_root / "alembic.ini")
    config.set_main_option("script_location", str(project_root / "agent-server/migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    test_app = app_module.create_app(
        database_url=database_url,
        data_dir=tmp_path,
        llm_factory=llm_factory,
    )
    return TestClient(test_app)


def _create_session(client: TestClient, *, domain: str = "general") -> str:
    response = client.post(
        "/sessions",
        json={
            "domain": domain,
            "title": "Monad learning demo",
            "goal": "Explain the increment() state change.",
        },
    )
    assert response.status_code == 200
    return str(response.json()["sessionId"])


class FakeCapabilityProvider:
    plugin_id = "demo"

    def tool_definitions(self) -> dict[str, type[ToolDefinition[Any, Any]]]:
        return {"FakeProjectionTool": FakeProjectionTool}

    def normalize_evidence_submission(self, request: object) -> object:
        if not isinstance(request, SubmitEvidenceRequest):
            return request
        if request.evidenceType != "monad_transaction":
            return request
        metadata = dict(request.metadata)
        raw_explanation = metadata.get("operationExplanation")
        explanation = raw_explanation.strip() if isinstance(raw_explanation, str) else ""
        if not explanation:
            return request
        return request.model_copy(update={"textContent": explanation, "metadata": metadata})

    def capability_definitions(self) -> Sequence[VerificationCapability]:
        return (
            VerificationCapability(
                registry_name="demo_projection",
                tool_class_name="FakeProjectionTool",
                supported_evidence_types=frozenset({"demo"}),
                supported_domains=frozenset({"*"}),
                priority=30,
                read_only=True,
                requires_network=False,
                timeout_seconds=5.0,
                enabled=True,
                version="1",
            ),
        )

    def public_capabilities(self) -> Sequence[PublicPluginCapability]:
        return (
            PublicPluginCapability(
                plugin_id="demo",
                capability_id="demo_projection",
                enabled=True,
                metadata={"label": "Demo projection"},
            ),
        )


class FakeProjectionTool(ToolDefinition[EvidenceReferenceAction, VerificationObservation]):
    name: ClassVar[str] = "fake_projection_tool"

    @classmethod
    def create(
        cls,
        conv_state: Any | None = None,
        *,
        session_id: str,
        repository: object | None = None,
    ) -> Sequence[Self]:
        del conv_state, session_id, repository
        return [
            cls(
                description="Project one generic verification observation.",
                action_type=EvidenceReferenceAction,
                observation_type=VerificationObservation,
                executor=FakeProjectionExecutor(),
                annotations=read_only_annotations("Fake projection"),
            )
        ]


class FakeProjectionExecutor(ToolExecutor[EvidenceReferenceAction, VerificationObservation]):
    def __call__(
        self,
        action: EvidenceReferenceAction,
        conversation: Any | None = None,
    ) -> VerificationObservation:
        del conversation
        now = utc_now()
        return VerificationObservation.from_text(
            "projected",
            evidence_id=action.evidence_id,
            capability="demo_projection",
            status="success",
            facts={"network": "demo", "transaction_hash": "0x" + "11" * 32},
            weak_signals=[],
            source_refs=[action.evidence_id],
            verifier_version="1",
            started_at=now,
            completed_at=now,
        )


class RecordingMonadVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[MonadEvidence, datetime]] = []

    def verify(self, evidence: MonadEvidence, session_started_at: datetime) -> MonadVerifierResult:
        self.calls.append((evidence, session_started_at))
        return MonadVerifierResult(
            "verified",
            {
                "chain_id": 1234,
                "transaction_hash": TX_HASH,
                "sender": WALLET,
                "target": CONTRACT,
                "previous_value": 0,
                "new_value": 1,
            },
            (),
            50,
            False,
        )


class BoundMonadRepository(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    _repository: SessionEvidenceRepository = PrivateAttr()
    _verifier: RecordingMonadVerifier = PrivateAttr()
    _claims: list[dict[str, Any]] = PrivateAttr()
    _principal_id: str = PrivateAttr()
    _uow_factory: Any = PrivateAttr()

    @classmethod
    def bind(
        cls,
        repository: SessionEvidenceRepository,
        verifier: RecordingMonadVerifier,
        claims: list[dict[str, Any]],
        *,
        principal_id: str,
        uow_factory: Any,
    ) -> "BoundMonadRepository":
        bound = cls()
        bound._repository = repository
        bound._verifier = verifier
        bound._claims = claims
        bound._principal_id = principal_id
        bound._uow_factory = uow_factory
        return bound

    @model_serializer
    def _serialize_without_runtime_binding(self) -> dict[str, Any]:
        return {}

    def get_monad_verifier(self) -> RecordingMonadVerifier:
        return self._verifier

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        stored = self._repository.get_evidence(session_id, evidence_id)
        metadata = dict(stored.metadata)
        metadata["explanation"] = stored.textContent or ""
        metadata["sessionStartedAt"] = self._session_started_at(session_id)
        return Evidence(
            evidenceId=stored.evidenceId,
            evidenceType=stored.evidenceType,
            contentHash=stored.contentHash,
            textContent=stored.textContent,
            sourceUrl=stored.sourceUrl,
            metadata=metadata,
        )

    def claim_monad_transaction(self, **values: Any) -> None:
        self._claims.append(values)

    def _session_started_at(self, session_id: str) -> str:
        with self._uow_factory() as uow:
            session = uow.sessions.get_owned(session_id, self._principal_id)
        if session is None:
            raise KeyError(f"Session {session_id} does not exist")
        started_at = session.created_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        return started_at.isoformat()


class FakeBoundMonadProvider:
    plugin_id = "monad-fixture"

    def __init__(self, verifier: RecordingMonadVerifier) -> None:
        self._verifier = verifier
        self.claims: list[dict[str, Any]] = []

    def tool_definitions(self) -> dict[str, type[ToolDefinition[Any, Any]]]:
        return {"MonadVerificationTool": MonadVerificationTool}

    def normalize_evidence_submission(self, request: object) -> object:
        if not isinstance(request, SubmitEvidenceRequest):
            return request
        if request.evidenceType != "monad_transaction":
            return request
        metadata = dict(request.metadata)
        raw_explanation = metadata.get("operationExplanation")
        explanation = raw_explanation.strip() if isinstance(raw_explanation, str) else ""
        if not explanation:
            return request
        return request.model_copy(update={"textContent": explanation, "metadata": metadata})

    def capability_definitions(self) -> Sequence[VerificationCapability]:
        return (
            VerificationCapability(
                registry_name="monad_learning_transaction",
                tool_class_name="MonadVerificationTool",
                supported_evidence_types=frozenset({"monad_transaction"}),
                supported_domains=frozenset({"*"}),
                priority=30,
                read_only=True,
                requires_network=True,
                timeout_seconds=8.0,
                enabled=True,
                version="1",
            ),
        )

    def bind_session_repository(
        self,
        repository: SessionEvidenceRepository,
        *,
        session_id: str,
        principal_id: str,
        uow_factory: Any,
    ) -> BoundMonadRepository:
        del session_id
        return BoundMonadRepository.bind(
            repository,
            self._verifier,
            self.claims,
            principal_id=principal_id,
            uow_factory=uow_factory,
        )


def _projection_llm(session_id: str) -> TestLLM:
    del session_id
    verify_call = MessageToolCall(
        id="call_demo_projection",
        name="fake_projection_tool",
        arguments=json.dumps({"evidence_id": "ev_demo"}),
        origin="completion",
    )
    draft_call = MessageToolCall(
        id="call_demo_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Verification observation was projected."],
                "understanding_findings": ["The learner explained the state change."],
                "contradictions": [],
                "recommended_next_step": "Describe the event payload.",
                "confidence": 0.7,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Run projection")],
                tool_calls=[verify_call],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Submit draft")],
                tool_calls=[draft_call],
            ),
        ]
    )


def _monad_review_llm(evidence_ids: dict[str, str], session_id: str) -> TestLLM:
    evidence_id = evidence_ids.get(session_id)
    if evidence_id is None:
        return TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="Waiting for evidence.")])]
        )
    verify_call = MessageToolCall(
        id="call_monad_verify",
        name="verify_monad_learning_transaction",
        arguments=json.dumps({"evidence_id": evidence_id}),
        origin="completion",
    )
    draft_call = MessageToolCall(
        id="call_monad_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Transaction verification facts were recorded."],
                "understanding_findings": [
                    "The learner linked the transaction to the increment task."
                ],
                "contradictions": [],
                "recommended_next_step": "Explain why the contract stores counts per learner.",
                "confidence": 0.75,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Verify the Monad transaction")],
                tool_calls=[verify_call],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Submit draft")],
                tool_calls=[draft_call],
            ),
        ]
    )


def test_disabled_session_view_omits_monad_capability(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    with _migrated_app(tmp_path, llm_factory=_projection_llm, monkeypatch=monkeypatch) as client:
        session_id = _create_session(client)
        response = client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["view"].get("pluginCapabilities", []) == []


def test_enabled_session_view_exposes_only_safe_monad_capability_fields(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    env = {
        "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "true",
        "FOCUSPROOF_MONAD_RPC_URL": "https://rpc.example.test",
        "FOCUSPROOF_MONAD_CHAIN_ID": "1234",
        "FOCUSPROOF_MONAD_CONTRACT_ADDRESS": CONTRACT,
        "FOCUSPROOF_MONAD_DEPLOYMENT_BLOCK": "42",
        "FOCUSPROOF_MONAD_EXPLORER_TX_BASE_URL": "https://explorer.example.test/tx/",
    }
    with _migrated_app(
        tmp_path,
        llm_factory=_projection_llm,
        monkeypatch=monkeypatch,
        env=env,
    ) as client:
        session_id = _create_session(client, domain="web3")
        response = client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        capabilities = response.json()["view"]["pluginCapabilities"]
        assert capabilities == [
            {
                "pluginId": "monad",
                "capabilityId": "monad_learning_transaction",
                "enabled": True,
                "metadata": {
                    "chainId": 1234,
                    "chainName": "Monad",
                    "contractAddress": CONTRACT,
                    "explorerTxBaseUrl": "https://explorer.example.test/tx/",
                    "operationLabel": "Call increment() on MonadLearningCounter",
                    "taskDescription": "Submit a wallet transaction that calls increment() on the configured teaching contract.",
                },
            }
        ]
        assert "rpcUrl" not in json.dumps(capabilities)


def test_create_app_loader_provider_projects_generic_verification_completed(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        app_module,
        "load_evidence_plugin_providers",
        lambda environ: (FakeCapabilityProvider(),),
    )
    with _migrated_app(tmp_path, llm_factory=_projection_llm, monkeypatch=monkeypatch) as client:
        session_id = _create_session(client)
        evidence_response = client.post(
            f"/sessions/{session_id}/evidence",
            json={
                "evidenceType": "demo",
                "textContent": "Projection should land in the generic verification view.",
                "metadata": {},
            },
        )
        assert evidence_response.status_code == 200
        manager = client.app.state.conversation_manager

        session_response = client.get(f"/sessions/{session_id}")
        owner_user_id = session_response.json()["state"]["ownerUserId"]
        handle = manager.get_or_restore(session_id, owner_user_id)
        try:
            handle.conversation.send_message("run demo projection")
            handle.conversation.run()
        finally:
            handle.conversation.close()
            manager._handles.pop(session_id, None)

        events = client.get(f"/sessions/{session_id}/events").json()["events"]
        verification = next(event for event in events if event["type"] == "verification.completed")
        assert verification["payload"]["capability"] == "demo_projection"


def test_plugin_enabled_review_e2e_uses_bound_monad_repository_and_server_normalized_metadata(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    verifier = RecordingMonadVerifier()
    provider = FakeBoundMonadProvider(verifier)
    evidence_ids: dict[str, str] = {}
    monkeypatch.setattr(
        app_module,
        "load_evidence_plugin_providers",
        lambda environ: (provider,),
    )
    with _migrated_app(
        tmp_path,
        llm_factory=lambda session_id: _monad_review_llm(evidence_ids, session_id),
        monkeypatch=monkeypatch,
    ) as client:
        manager = client.app.state.conversation_manager
        original_get_or_restore = manager.get_or_restore

        def _skip_bootstrap(*args: Any, **kwargs: Any) -> Any:
            raise ValueError("skip bootstrap")

        monkeypatch.setattr(manager, "get_or_restore", _skip_bootstrap)
        session_id = _create_session(client, domain="web3")
        monkeypatch.setattr(manager, "get_or_restore", original_get_or_restore)
        payload = {
            "evidenceType": "monad_transaction",
            "metadata": {
                "walletAddress": WALLET,
                "transactionHash": TX_HASH,
                "contractAddress": CONTRACT,
                "operationExplanation": "I used the deterministic increment fixture transaction.",
            },
        }
        session_response = client.get(f"/sessions/{session_id}")
        owner_user_id = session_response.json()["state"]["ownerUserId"]
        handle = manager.get_or_restore(session_id, owner_user_id)
        handle.conversation.close()
        manager._handles.pop(session_id, None)

        evidence_response = client.post(f"/sessions/{session_id}/evidence", json=payload)
        assert evidence_response.status_code == 200
        evidence_ids[session_id] = evidence_response.json()["evidenceId"]

        handle = manager.get_or_restore(session_id, owner_user_id)
        handle.conversation.close()
        manager._handles.pop(session_id, None)

        review_response = client.post(f"/sessions/{session_id}/review", json={})
        assert review_response.status_code == 200, review_response.json()
        assert review_response.json()["reviewStatus"] == "completed"

        assert len(verifier.calls) == 1
        verified_evidence, session_started_at = verifier.calls[0]
        assert verified_evidence.wallet_address == WALLET
        assert verified_evidence.transaction_hash == TX_HASH
        assert verified_evidence.explanation == payload["metadata"]["operationExplanation"]
        assert session_started_at.tzinfo is not None

        events = client.get(f"/sessions/{session_id}/events").json()["events"]
        verification = next(event for event in events if event["type"] == "verification.completed")
        assert verification["payload"]["capability"] == "monad_learning_transaction"
        assert provider.claims[0]["evidence_id"] == evidence_ids[session_id]
