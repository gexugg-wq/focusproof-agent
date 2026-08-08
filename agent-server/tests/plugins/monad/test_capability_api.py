from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Self

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import ToolDefinition, ToolExecutor

from focusproof.api import app as app_module
from focusproof.domain.plugins.base import PublicPluginCapability
from focusproof.openhands_runtime.capabilities import VerificationCapability
from focusproof.openhands_runtime.tools import (
    EvidenceReferenceAction,
    VerificationObservation,
    read_only_annotations,
    utc_now,
)


CONTRACT = "0x52908400098527886E0F7030069857D2E4169EE7"


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


class FakeProjectionTool(
    ToolDefinition[EvidenceReferenceAction, VerificationObservation]
):
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


class FakeProjectionExecutor(
    ToolExecutor[EvidenceReferenceAction, VerificationObservation]
):
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

        session_response = client.get(f"/sessions/{session_id}")
        owner_user_id = session_response.json()["state"]["ownerUserId"]
        manager = client.app.state.conversation_manager
        handle = manager.get_or_restore(session_id, owner_user_id)
        try:
            handle.conversation.send_message("run demo projection")
            handle.conversation.run()
        finally:
            handle.conversation.close()
            manager._handles.pop(session_id, None)

        events = client.get(f"/sessions/{session_id}/events").json()["events"]
        verification = next(
            event for event in events if event["type"] == "verification.completed"
        )
        assert verification["payload"]["capability"] == "demo_projection"
