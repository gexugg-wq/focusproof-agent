from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from openhands.sdk import Agent, Conversation
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool

from focusproof.openhands_runtime.prompts import FOCUSPROOF_SYSTEM_PROMPT
from focusproof.openhands_runtime.tools.evidence_verification import (
    EvidenceVerificationAction,
    EvidenceVerificationObservation,
)
from focusproof.openhands_runtime.tool_registry import (
    ensure_focusproof_tools_registered,
)
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.event_log import InMemoryEventLog


LEGACY_TOOL_CLASSES = (
    "FocusProofEvidenceVerificationTool",
    "FocusProofLearnerInputTool",
    "FocusProofReviewDraftTool",
)


class UpgradeRepository:
    def __init__(self) -> None:
        self.evidence = Evidence(
            evidenceId="ev_upgrade",
            evidenceType="text",
            contentHash="sha256:upgrade",
            textContent=(
                "Append-only events preserve the original record while projections "
                "can be rebuilt from the native history."
            ),
        )

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        assert session_id == "sess_upgrade"
        assert evidence_id == self.evidence.evidenceId
        return self.evidence


def _goal() -> LearningGoal:
    return LearningGoal(
        domain="general",
        title="Understand event replay",
        goal="Explain how append-only events rebuild a projection.",
    )


def _legacy_llm() -> TestLLM:
    return TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="unused")])]
    )


def _restored_llm(session_id: str) -> TestLLM:
    del session_id
    verification = MessageToolCall(
        id="call_upgrade_text",
        name="focusproof_text_evidence_verification",
        arguments=json.dumps({"evidence_id": "ev_upgrade"}),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Verify with the AI4A text tool")],
                tool_calls=[verification],
            )
        ]
    )


def _legacy_action_and_observation() -> tuple[ActionEvent, ObservationEvent]:
    tool_call = MessageToolCall(
        id="call_upgrade_legacy",
        name="focusproof_evidence_verification",
        arguments=json.dumps({"evidence_id": "ev_upgrade"}),
        origin="completion",
    )
    action = ActionEvent(
        thought=[TextContent(text="Run the legacy verifier")],
        action=EvidenceVerificationAction(evidence_id="ev_upgrade"),
        tool_name="focusproof_evidence_verification",
        tool_call_id=tool_call.id,
        tool_call=tool_call,
        llm_response_id="response_upgrade_legacy",
    )
    observation = ObservationEvent(
        tool_name=action.tool_name,
        tool_call_id=action.tool_call_id,
        observation=EvidenceVerificationObservation.from_text(
            "Legacy repository verification completed.",
            evidence_id="ev_upgrade",
            verified=True,
            evidence_type="text",
            findings=["Legacy evidence contains a specific explanation."],
            weak_signals=["Legacy verified is not a final learning verdict."],
            source_refs=["ev_upgrade", "sha256:upgrade"],
            verifier="focusproof-session-repository",
        ),
        action_id=action.id,
    )
    return action, observation


def _persist_legacy_conversation(
    *,
    project_root: Path,
    session_id: str,
    conversation_id: UUID,
) -> tuple[list[str], list[str]]:
    ensure_focusproof_tools_registered()
    runtime_root = project_root / "var" / "conversations" / session_id
    conversation = Conversation(
        agent=Agent(
            llm=_legacy_llm(),
            tools=[
                Tool(name=name, params={"session_id": session_id})
                for name in LEGACY_TOOL_CLASSES
            ],
            include_default_tools=[],
            system_prompt=FOCUSPROOF_SYSTEM_PROMPT,
        ),
        workspace=runtime_root / "workspace",
        persistence_dir=runtime_root / "persistence",
        conversation_id=conversation_id,
        visualizer=None,
        delete_on_close=False,
    )
    try:
        cast(Any, conversation).send_message(
            json.dumps(
                {
                    "kind": "evidence",
                    "session_id": session_id,
                    "evidence": {
                        "evidenceId": "ev_upgrade",
                        "evidenceType": "text",
                        "contentHash": "sha256:upgrade",
                    },
                },
                sort_keys=True,
            )
        )
        action, observation = _legacy_action_and_observation()
        conversation.state.append_event(action)
        conversation.state.append_event(observation)
        events = list(conversation.state.events)
        return (
            [event.id for event in events],
            [event.model_dump_json() for event in events],
        )
    finally:
        conversation.close()


def test_base_conversation_restores_into_ai4a_without_rewriting_history(
    tmp_path: Path,
) -> None:
    from focusproof.openhands_runtime.factory import ConversationFactory
    from focusproof.openhands_runtime.projector import OpenHandsEventProjector

    session_id = "sess_upgrade"
    conversation_id = uuid4()
    original_ids, original_json = _persist_legacy_conversation(
        project_root=tmp_path,
        session_id=session_id,
        conversation_id=conversation_id,
    )
    handle_box: dict[str, Any] = {}

    def pause_after_new_verification(
        callback_session_id: str,
        callback_conversation_id: UUID,
    ) -> Any:
        assert callback_session_id == session_id
        assert callback_conversation_id == conversation_id

        def callback(event: Any) -> None:
            if (
                isinstance(event, ObservationEvent)
                and event.tool_name == "focusproof_text_evidence_verification"
            ):
                handle_box["handle"].conversation.pause()

        return callback

    factory = ConversationFactory(
        project_root=tmp_path,
        repository=UpgradeRepository(),
        llm_factory=_restored_llm,
        callback_factory=pause_after_new_verification,
    )
    restored = factory.create(
        session_id,
        _goal(),
        conversation_id=conversation_id,
    )
    handle_box["handle"] = restored
    audit_log = InMemoryEventLog()
    first_projector = OpenHandsEventProjector(
        session_id,
        conversation_id,
        audit_log,
    )
    try:
        restored_events = list(restored.conversation.state.events)
        assert restored.conversation_id == conversation_id
        assert [event.id for event in restored_events] == original_ids
        assert [event.model_dump_json() for event in restored_events] == original_json
        assert first_projector.reconcile(restored_events) == 3

        cast(Any, restored.conversation).run()
        events_after_run = list(restored.conversation.state.events)
        assert set(restored.conversation.agent.tools_map) == {
            "focusproof_evidence_verification",
            "focusproof_learner_input",
            "focusproof_review_draft",
            "focusproof_text_evidence_verification",
            "focusproof_url_evidence_verification",
        }
        assert [event.id for event in events_after_run[: len(original_ids)]] == (
            original_ids
        )
        assert any(
            isinstance(event, ObservationEvent)
            and event.tool_name == "focusproof_text_evidence_verification"
            for event in events_after_run[len(original_ids) :]
        )
        assert first_projector.reconcile(events_after_run) == 2
        first_audit_count = audit_log.count(session_id)
    finally:
        restored.conversation.close()

    reopened = ConversationFactory(
        project_root=tmp_path,
        repository=UpgradeRepository(),
        llm_factory=_restored_llm,
    ).create(
        session_id,
        _goal(),
        conversation_id=conversation_id,
    )
    try:
        reopened_events = list(reopened.conversation.state.events)
        assert [event.id for event in reopened_events[: len(original_ids)]] == (
            original_ids
        )
        second_projector = OpenHandsEventProjector(
            session_id,
            conversation_id,
            audit_log,
        )
        assert second_projector.reconcile(reopened_events) == 0
        assert second_projector.reconcile(reopened_events) == 0
        assert audit_log.count(session_id) == first_audit_count
    finally:
        reopened.conversation.close()
