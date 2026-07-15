from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM

from focusproof.domain.scoring import score_learning_session
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.observations import Observation

TEXT_EVIDENCE_ID = "ev_22222222222222222222222222222222"
URL_EVIDENCE_ID = "ev_33333333333333333333333333333333"


@dataclass(frozen=True)
class GeneralDomainCase:
    domain: str
    title: str
    goal: str
    text: str
    answer: str


GENERAL_DOMAIN_CASES = (
    GeneralDomainCase(
        domain="programming",
        title="Understand event replay",
        goal="Explain how an append-only event log rebuilds application state.",
        text=(
            "I first mutated the current view directly and lost history. The correction is "
            "to append immutable events, then fold them in sequence to rebuild the view."
        ),
        answer=(
            "Replay starts with an empty state and applies each ordered event. A later view can "
            "be reproduced because earlier events are retained rather than overwritten."
        ),
    ),
    GeneralDomainCase(
        domain="mathematics",
        title="Explain the quadratic formula",
        goal="Derive the quadratic formula by completing the square.",
        text=(
            "Divide ax²+bx+c=0 by a, move the constant, and add (b/2a)² to both sides. "
            "Taking square roots and isolating x gives (-b±√(b²-4ac))/(2a)."
        ),
        answer=(
            "The plus-or-minus appears because a nonnegative square has two square roots. "
            "The discriminant determines whether those roots are distinct, repeated, or non-real."
        ),
    ),
    GeneralDomainCase(
        domain="language",
        title="Revise an English paragraph",
        goal="Use tense consistently and explain a self-correction.",
        text=(
            "My draft said 'Yesterday I go to the library.' I corrected go to went because the "
            "time marker places the completed action in the simple past."
        ),
        answer=(
            "I would keep present tense only for a current habit, such as 'I go every Tuesday.' "
            "The original sentence describes one completed visit yesterday."
        ),
    ),
    GeneralDomainCase(
        domain="reading",
        title="Recall an argument structure",
        goal="Summarize a chapter and distinguish its claim from supporting evidence.",
        text=(
            "The chapter claims that durable memory depends on retrieval rather than rereading. "
            "It supports the claim with delayed tests, then limits it by noting feedback matters."
        ),
        answer=(
            "The delayed-test result is evidence for the claim, while the feedback limitation is "
            "a qualification. My summary keeps those roles separate instead of listing details."
        ),
    ),
)


class _FixedUuid:
    def __init__(self, value: str) -> None:
        self.hex = value


def _scripted_follow_up_llm(_: str) -> TestLLM:
    verify = MessageToolCall(
        id="call_ai4b_verify",
        name="focusproof_text_evidence_verification",
        arguments=json.dumps({"evidence_id": TEXT_EVIDENCE_ID}),
        origin="completion",
    )
    question = MessageToolCall(
        id="call_ai4b_question",
        name="focusproof_learner_input",
        arguments=json.dumps(
            {
                "question": "Explain the central idea in your own words and give one reason.",
                "reason": "A focused explanation is needed before the final review.",
                "requested_evidence_type": "text",
            }
        ),
        origin="completion",
    )
    draft = MessageToolCall(
        id="call_ai4b_draft",
        name="focusproof_review_draft",
        arguments=json.dumps(
            {
                "credibility_findings": ["Repository-backed evidence was inspected."],
                "understanding_findings": ["The learner supplied a concrete explanation."],
                "contradictions": [],
                "recommended_next_step": "Practice one related example.",
                "confidence": 0.72,
            }
        ),
        origin="completion",
    )
    return TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Inspect the text evidence")],
                tool_calls=[verify],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Ask one focused question")],
                tool_calls=[question],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="Submit the final review draft")],
                tool_calls=[draft],
            ),
        ]
    )


def _create_session(client: TestClient, case: GeneralDomainCase) -> str:
    response = client.post(
        "/sessions",
        json={
            "domain": case.domain,
            "title": case.title,
            "goal": case.goal,
            "expectedOutput": "A concrete explanation",
            "plannedMinutes": 30,
        },
    )
    assert response.status_code == 200
    return str(response.json()["sessionId"])


@pytest.mark.parametrize("case", GENERAL_DOMAIN_CASES, ids=lambda case: case.domain)
def test_general_domain_uses_real_fastapi_and_native_openhands_flow(
    case: GeneralDomainCase,
    ai4b_app_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uuid_values = iter(
        [
            _FixedUuid("1" * 32),
            _FixedUuid("2" * 32),
            _FixedUuid("3" * 32),
        ]
    )
    monkeypatch.setattr("focusproof.api.app.uuid4", lambda: next(uuid_values))

    with ai4b_app_factory(_scripted_follow_up_llm) as running:
        session_id = _create_session(running.client, case)
        text_response = running.client.post(
            f"/sessions/{session_id}/evidence",
            json={"evidenceType": "text", "textContent": case.text},
        )
        url_response = running.client.post(
            f"/sessions/{session_id}/evidence",
            json={
                "evidenceType": "url",
                "sourceUrl": f"https://example.com/{case.domain}",
                "textContent": "This source supports the explanation recorded in my notes.",
            },
        )
        assert text_response.status_code == 200
        assert text_response.json()["evidenceId"] == TEXT_EVIDENCE_ID
        assert url_response.status_code == 200
        assert url_response.json()["evidenceId"] == URL_EVIDENCE_ID

        first_review = running.client.post(f"/sessions/{session_id}/review")
        assert first_review.status_code == 200
        first_result = first_review.json()
        assert first_result["reviewStatus"] == "awaiting_user"
        assert len(first_result["agentQuestions"]) == 1
        question = first_result["agentQuestions"][0]

        answer_response = running.client.post(
            f"/sessions/{session_id}/answer",
            json={"questionId": question["questionId"], "answer": case.answer},
        )
        assert answer_response.status_code == 200
        completed_response = running.client.post(f"/sessions/{session_id}/review")
        assert completed_response.status_code == 200
        completed = completed_response.json()
        assert completed["reviewStatus"] == "completed"
        assert completed["reviewResult"] is not None
        assert completed["usedOpenHandsConversation"] is True

        handle = running.app.state.conversation_manager.get(session_id)
        native_events = list(handle.conversation.state.events)
        verification_action = next(
            event
            for event in native_events
            if isinstance(event, ActionEvent)
            and event.tool_name == "focusproof_text_evidence_verification"
        )
        verification_observation = next(
            event
            for event in native_events
            if isinstance(event, ObservationEvent)
            and event.tool_name == "focusproof_text_evidence_verification"
        )
        assert verification_action.tool_call_id == verification_observation.tool_call_id
        assert native_events.index(verification_action) < native_events.index(
            verification_observation
        )
        assert any(isinstance(event, MessageEvent) for event in native_events)

        build_log = running.client.get(f"/sessions/{session_id}/events").json()[
            "events"
        ]
        sequences = [event["sequence"] for event in build_log]
        assert sequences == sorted(sequences)
        assert any(event["type"] == "verification.requested" for event in build_log)
        assert any(event["type"] == "verification.completed" for event in build_log)
        assert any(event["type"] == "review.completed" for event in build_log)


def _evidence(text: str, *, evidence_id: str = "ev_quality") -> Evidence:
    return Evidence(
        evidenceId=evidence_id,
        evidenceType="text",
        contentHash="sha256:quality",
        textContent=text,
    )


def _goal(goal: str) -> LearningGoal:
    return LearningGoal(domain="general", title="Learning goal", goal=goal)


def _successful_text_observation(*, weak_signals: list[str]) -> Observation:
    return Observation(
        toolName="focusproof_text_evidence_verification",
        status="success",
        facts={"has_text": True, "weak_signals": weak_signals},
        sourceRefs=["ev_quality", "sha256:quality"],
    )


def test_vague_notes_never_receive_high_confidence() -> None:
    result = score_learning_session(
        goal=_goal("Explain event replay with a concrete example."),
        evidence=[_evidence("I learned a lot.")],
        answers=[],
        observations=[
            _successful_text_observation(
                weak_signals=["text_too_short", "generic_learning_claim"]
            )
        ],
    )

    assert result.status in {"WeakEvidence", "InsufficientEvidence"}
    assert result.score < 60
    assert result.confidence < 0.7


def test_goal_copy_is_not_independent_evidence() -> None:
    copied = "Explain immutable event replay by rebuilding application state in sequence."
    result = score_learning_session(
        goal=_goal(copied),
        evidence=[_evidence(copied)],
        answers=[],
    )

    assert result.status in {"WeakEvidence", "InsufficientEvidence"}
    assert result.score < 60


def test_goal_evidence_mismatch_is_reported() -> None:
    result = score_learning_session(
        goal=_goal("Derive the quadratic formula by completing the square."),
        evidence=[
            _evidence(
                "English past tense describes a completed action, while present tense can describe a habit."
            )
        ],
        answers=[],
    )

    assert result.status == "WeakEvidence"
    assert result.dimensions["goalAlignment"] < 15


def test_correct_reflection_can_support_an_error_record() -> None:
    result = score_learning_session(
        goal=_goal("Explain how event replay rebuilds state."),
        evidence=[
            _evidence(
                "I incorrectly mutated the view and lost history. I corrected it by appending "
                "each event and replaying the ordered events to rebuild state from an empty view."
            )
        ],
        answers=[],
    )

    assert result.status == "LikelyLearning"
    assert result.score >= 60


def test_strong_follow_up_can_improve_support() -> None:
    goal = _goal("Explain how event replay rebuilds state.")
    evidence = [
        _evidence(
            "Events are immutable records applied in sequence to reconstruct application state."
        )
    ]
    before = score_learning_session(goal=goal, evidence=evidence, answers=[])
    after = score_learning_session(
        goal=goal,
        evidence=evidence,
        answers=[
            "Starting from an empty state, the reducer applies each event once in order, so a "
            "view can be reproduced without overwriting the history."
        ],
    )

    assert after.score > before.score
    assert after.dimensions["understanding"] > before.dimensions["understanding"]


def test_elapsed_time_alone_never_proves_learning() -> None:
    result = score_learning_session(
        goal=_goal("Explain event replay with a concrete example."),
        evidence=[_evidence("I spent 120 minutes studying this topic today.")],
        answers=[],
    )

    assert result.status in {"WeakEvidence", "InsufficientEvidence"}
    assert result.score < 60
