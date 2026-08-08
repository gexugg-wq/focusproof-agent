from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
AGENT_SERVER = ROOT / "agent-server"
if str(AGENT_SERVER) not in sys.path:
    sys.path.insert(0, str(AGENT_SERVER))

import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
import focusproof.api.app  # noqa: E402
from focusproof.api.models import SubmitEvidenceRequest  # noqa: E402
from focusproof.domain.plugins.base import EvidencePluginProvider, PublicPluginCapability  # noqa: E402
from focusproof.domain.plugins.monad.models import MonadVerificationObservation  # noqa: E402
from focusproof.domain.plugins.monad.tool import MonadVerificationTool  # noqa: E402
from focusproof.domain.plugins.monad.session_repository import BoundMonadSessionRepository  # noqa: E402
from focusproof.domain.plugins.monad.verifier import MonadEvidenceVerifier  # noqa: E402
from focusproof.openhands_runtime.capabilities import VerificationCapability  # noqa: E402
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike  # noqa: E402
from focusproof.openhands_runtime.tools import SessionEvidenceRepository  # noqa: E402
from openhands.sdk.llm import Message, MessageToolCall, TextContent  # noqa: E402
import openhands.sdk.testing  # noqa: E402

LOOPBACK_HOST = "127.0.0.1"
SMOKE_EVIDENCE_TEXT = (
    "Append-only event replay rebuilds state by applying immutable events in "
    "sequence, preserving the history needed to reproduce the current view."
)
MONAD_WALLET = "0xde709f2102306220921060314715629080e2fb77"
MONAD_CONTRACT = "0x52908400098527886E0F7030069857D2E4169EE7"
MONAD_TX_HASH = "0x" + "ab" * 32
MONAD_EXPLANATION = "I used the deterministic demo transaction to call increment()."
MONAD_CHAIN_ID = 1234
MONAD_EXPLORER = "https://explorer.example.test/tx/"


class _MonadVerifier:
    def verify(
        self, evidence: object, session_started_at: datetime
    ) -> MonadVerificationObservation:
        return MonadVerificationObservation(
            "verified",
            {
                "chain_id": MONAD_CHAIN_ID,
                "transaction_hash": MONAD_TX_HASH,
                "sender": MONAD_WALLET,
                "target": MONAD_CONTRACT,
                "previous_value": 0,
                "new_value": 1,
            },
            (),
            50,
            False,
        )


class _MonadDemoProvider:
    plugin_id = "monad"

    def tool_definitions(self) -> Mapping[str, type[object]]:
        return {"MonadVerificationTool": MonadVerificationTool}

    def public_capabilities(self) -> tuple[PublicPluginCapability, ...]:
        return (
            PublicPluginCapability(
                plugin_id="monad",
                capability_id="monad_learning_transaction",
                enabled=True,
                metadata={
                    "chainId": MONAD_CHAIN_ID,
                    "chainName": "Monad",
                    "contractAddress": MONAD_CONTRACT,
                    "explorerTxBaseUrl": MONAD_EXPLORER,
                    "operationLabel": "Call increment() on MonadLearningCounter",
                    "taskDescription": "Submit a wallet transaction that calls increment() on the configured teaching contract.",
                },
            ),
        )

    def capability_definitions(self) -> tuple[VerificationCapability, ...]:
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
        metadata.update(explanation=explanation, sessionStartedAt=datetime.now(UTC).isoformat())
        return request.model_copy(update={"textContent": explanation, "metadata": metadata})

    def bind_session_repository(
        self,
        repository: SessionEvidenceRepository,
        *,
        session_id: str,
        principal_id: str,
        uow_factory: object,
    ) -> BoundMonadSessionRepository:
        del session_id
        return BoundMonadSessionRepository.bind(
            repository,
            verifier=cast(MonadEvidenceVerifier, _MonadVerifier()),
            principal_id=principal_id,
            uow_factory=cast(UnitOfWorkFactoryLike, uow_factory),
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the production FocusProof FastAPI application with a deterministic "
            "OpenHands SDK TestLLM on IPv4 loopback only."
        )
    )
    parser.add_argument("--host", required=True, choices=(LOOPBACK_HOST,))
    parser.add_argument("--port", required=True, type=_port)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--scenario", required=True, choices=("general-flow", "monad-flow"))
    return parser.parse_args(argv)


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _apply_migrations(database_url: str) -> None:
    config = Config(ROOT / "alembic.ini")
    config.set_main_option(
        "script_location",
        str(ROOT / "agent-server" / "migrations"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _general_flow_llm_factory(session_id: str) -> openhands.sdk.testing.TestLLM:
    evidence_id = focusproof.api.app._evidence_id_for_request(
        session_id,
        SubmitEvidenceRequest(
            evidenceType="text",
            textContent=SMOKE_EVIDENCE_TEXT,
        ),
    )
    verify = MessageToolCall(
        id=f"verify_{session_id}",
        name="focusproof_text_evidence_verification",
        arguments=json.dumps({"evidence_id": evidence_id}),
        origin="completion",
    )
    question = MessageToolCall(
        id=f"question_{session_id}",
        name="focusproof_learner_input",
        arguments=json.dumps(
            {
                "question": ("Explain why retaining earlier events makes replay reproducible."),
                "reason": "The final review needs an independent learner explanation.",
                "requested_evidence_type": "text",
            }
        ),
        origin="completion",
    )
    draft = MessageToolCall(
        id=f"draft_{session_id}",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Repository-backed text evidence was inspected."],
                "understanding_findings": ["The learner supplied a concrete replay explanation."],
                "contradictions": [],
                "recommended_next_step": "Apply replay to one additional event sequence.",
                "confidence": 0.72,
            }
        ),
        origin="completion",
    )
    return openhands.sdk.testing.TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Inspect the submitted evidence.")],
                tool_calls=[verify],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Request one independent explanation.")],
                tool_calls=[question],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Submit the completed review draft.")],
                tool_calls=[draft],
            ),
        ]
    )


def _monad_flow_llm_factory(session_id: str) -> openhands.sdk.testing.TestLLM:
    evidence_id = focusproof.api.app._evidence_id_for_request(
        session_id,
        SubmitEvidenceRequest(
            evidenceType="monad_transaction",
            textContent=MONAD_EXPLANATION,
            metadata={
                "walletAddress": MONAD_WALLET,
                "transactionHash": MONAD_TX_HASH,
                "contractAddress": MONAD_CONTRACT,
                "operationExplanation": MONAD_EXPLANATION,
            },
        ),
    )
    verify = MessageToolCall(
        id=f"verify_monad_{session_id}",
        name="verify_monad_learning_transaction",
        arguments=json.dumps({"evidence_id": evidence_id}),
        origin="completion",
    )
    draft = MessageToolCall(
        id=f"draft_monad_{session_id}",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": [
                    "The transaction facts match the configured Monad increment task."
                ],
                "understanding_findings": [
                    "The learner linked the transaction to the increment transition."
                ],
                "contradictions": [],
                "recommended_next_step": "Explain why the contract stores a per-sender counter.",
                "confidence": 0.76,
            }
        ),
        origin="completion",
    )
    return openhands.sdk.testing.TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Verify the Monad transaction.")],
                tool_calls=[verify],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Submit the Monad review draft.")],
                tool_calls=[draft],
            ),
        ]
    )


def _scenario_factory(
    scenario: str,
) -> Callable[[str], openhands.sdk.testing.TestLLM]:
    if scenario == "general-flow":
        return _general_flow_llm_factory
    if scenario == "monad-flow":
        provider_loader = cast(
            Callable[[Mapping[str, str]], tuple[EvidencePluginProvider, ...]],
            lambda environ: (_MonadDemoProvider(),),
        )
        setattr(focusproof.api.app, "load_evidence_plugin_providers", provider_loader)
        return _monad_flow_llm_factory
    raise ValueError(f"Unsupported deterministic scenario: {scenario}")


def build_app(args: argparse.Namespace) -> FastAPI:
    if args.host != LOOPBACK_HOST:
        raise ValueError("AI4B test server is restricted to 127.0.0.1")
    data_dir = Path(args.data_dir).resolve()
    database_url = str(args.database_url)
    focusproof.api.app._validate_database_path(database_url, data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    _apply_migrations(database_url)
    return focusproof.api.app.create_app(
        database_url=database_url,
        data_dir=data_dir,
        llm_factory=_scenario_factory(str(args.scenario)),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    app = build_app(args)
    uvicorn.run(
        app,
        host=LOOPBACK_HOST,
        port=args.port,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
