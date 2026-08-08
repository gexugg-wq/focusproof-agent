import json

import pytest
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM

from focusproof.runtime.evidence import Evidence, LearningGoal


class SessionRepository:
    def __init__(self) -> None:
        self.evidence: dict[tuple[str, str], Evidence] = {}

    def add_evidence(self, session_id: str, evidence: Evidence) -> None:
        self.evidence[(session_id, evidence.evidenceId)] = evidence

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        return self.evidence[(session_id, evidence_id)]


@pytest.fixture
def learning_goal() -> LearningGoal:
    return LearningGoal(
        domain="general",
        title="Learn event replay",
        goal="Explain append-only event replay.",
    )


@pytest.fixture
def evidence() -> Evidence:
    return Evidence(
        evidenceId="ev_1",
        evidenceType="text",
        contentHash="sha256:test",
        textContent="Append-only events replay into a current immutable learning view.",
    )


@pytest.fixture
def repository() -> SessionRepository:
    return SessionRepository()


def completed_review_llm(session_id: str) -> TestLLM:
    del session_id
    verification_call = MessageToolCall(
        id="call_verify_1",
        name="focusproof_text_evidence_verification",
        arguments=json.dumps({"evidence_id": "ev_1"}),
        origin="completion",
    )
    draft_call = MessageToolCall(
        id="call_draft_1",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Evidence is repository-backed."],
                "understanding_findings": ["The explanation names append and replay."],
                "contradictions": [],
                "recommended_next_step": "Explain branch replay with one example.",
                "confidence": 0.75,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Verify the evidence")],
                tool_calls=[verification_call],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Submit the evidence-based draft")],
                tool_calls=[draft_call],
            ),
            Message(role="assistant", content=[TextContent(text="Review draft submitted")]),
        ]
    )


def awaiting_user_llm(session_id: str) -> TestLLM:
    del session_id
    question_call = MessageToolCall(
        id="call_question_1",
        name="focusproof_learner_input",
        arguments=json.dumps(
            {
                "question": "How does replay rebuild the current view?",
                "reason": "The submitted evidence does not explain replay semantics.",
                "requested_evidence_type": "text",
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Ask for a focused explanation")],
                tool_calls=[question_call],
            )
        ]
    )
