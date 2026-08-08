from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM

from focusproof.domain.plugins.monad.configuration import MonadPluginSettings
from focusproof.domain.plugins.monad.manifest import MonadEvidencePluginProvider
from focusproof.domain.plugins.monad.models import MonadVerificationObservation
from focusproof.openhands_runtime.capabilities import (
    VerificationCapabilityRegistry,
    build_builtin_capabilities,
)
from focusproof.openhands_runtime.tool_assembler import SessionToolAssembler
from focusproof.runtime.evidence import Evidence, LearningGoal


class Verifier:
    def verify(self, evidence: object, session_started_at: object) -> MonadVerificationObservation:
        return MonadVerificationObservation(
            "verified",
            {
                "chain_id": 1234,
                "transaction_hash": "0x" + "ab" * 32,
                "sender": "0xde709f2102306220921060314715629080e2fb77",
                "target": "0x52908400098527886E0F7030069857D2E4169EE7",
                "previous_value": 0,
                "new_value": 1,
            },
            (),
            50,
            False,
        )


class Repository:
    def __init__(self) -> None:
        self.claims: list[dict[str, Any]] = []

    def get_monad_verifier(self) -> Verifier:
        return Verifier()

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        return Evidence(
            evidenceId=evidence_id,
            evidenceType="monad_transaction",
            contentHash="sha256:public",
            metadata={
                "walletAddress": "0xde709f2102306220921060314715629080e2fb77",
                "transactionHash": "0x" + "ab" * 32,
                "explanation": "I observed the increment transition.",
                "sessionStartedAt": "2023-11-14T22:13:20+00:00",
            },
        )

    def claim_monad_transaction(self, **values: Any) -> None:
        self.claims.append(values)


def test_conversation_produces_native_action_and_observation_events(tmp_path: Path) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory

    settings = MonadPluginSettings(
        True,
        "https://rpc.example.test",
        1234,
        "0x52908400098527886E0F7030069857D2E4169EE7",
        42,
        "https://explorer.example.test/tx/",
    )
    assembler = SessionToolAssembler(
        VerificationCapabilityRegistry(build_builtin_capabilities()),
        plugin_providers=(MonadEvidencePluginProvider(settings),),
    )
    call = MessageToolCall(
        id="call_monad_1",
        name="verify_monad_learning_transaction",
        arguments=json.dumps({"evidence_id": "ev_1"}),
        origin="completion",
    )
    llm = TestLLM.from_messages(
        [
            Message(role="assistant", content=[TextContent(text="verify")], tool_calls=[call]),
            Message(role="assistant", content=[TextContent(text="done")]),
        ]
    )
    repository = Repository()
    factory = ConversationFactory(
        repository=repository,
        project_root=tmp_path,
        compatibility_mode=True,
        llm_factory=lambda _: llm,
        tool_assembler=assembler,
    )
    handle = factory.create(
        "sess_monad",
        LearningGoal(domain="general", title="Monad", goal="Explain increment"),
        evidence_types={"monad_transaction"},
    )
    try:
        cast(Any, handle.conversation).send_message("review ev_1")
        handle.conversation.run()
        events = list(handle.conversation.state.events)
        action = next(
            event
            for event in events
            if isinstance(event, ActionEvent)
            and event.tool_name == "verify_monad_learning_transaction"
        )
        observation = next(
            event
            for event in events
            if isinstance(event, ObservationEvent)
            and event.tool_name == "verify_monad_learning_transaction"
        )
        assert action.tool_call_id == observation.tool_call_id == "call_monad_1"
        assert action.id != observation.id
        assert repository.claims
    finally:
        handle.conversation.close()
