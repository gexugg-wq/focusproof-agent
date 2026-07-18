from focusproof.domain.review import Finding, ReviewResult
from focusproof.runtime.actions import Action
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.observations import Observation
from focusproof.runtime.view import AgentView, SessionView


def test_focusproof_models_serialize_without_frontend_or_contract_dependencies() -> None:
    goal = LearningGoal(
        domain="general",
        title="Learn event sourcing",
        goal="Explain append-only event logs with an example",
        expectedOutput="Short notes",
        plannedMinutes=30,
    )
    evidence = Evidence(
        evidenceId="ev_1",
        evidenceType="text",
        contentHash="sha256:abc",
        textContent="I learned that events are appended and replayed into views.",
    )
    action = Action(
        type="verify_evidence",
        toolName="focusproof_text_evidence_verification",
        input={},
        evidenceIds=["ev_1"],
    )
    observation = Observation(
        toolName="focusproof_text_evidence_verification",
        status="success",
        facts={"specific": True},
        sourceRefs=["ev_1"],
    )
    review = ReviewResult(
        status="LikelyLearning",
        score=68,
        confidence=0.7,
        dimensions={"understanding": 18},
        findings=[
            Finding(
                severity="info",
                message="Evidence explains event replay.",
                evidenceIds=["ev_1"],
                observationRefs=["obs_1"],
            )
        ],
        summary="The learner provided a concrete explanation.",
        nextStep="Add a worked example.",
    )
    view = AgentView(
        session=SessionView(id="s_1", status="running"),
        goal=goal,
        evidence=[evidence],
        verificationResults=[observation],
        findings=review.findings,
        unansweredQuestions=[],
        availableTools=[],
        previousActions=[action],
    )

    assert view.model_dump()["goal"]["domain"] == "general"
    assert review.model_dump()["findings"][0]["evidenceIds"] == ["ev_1"]
