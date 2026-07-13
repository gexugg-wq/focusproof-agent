from focusproof.domain.scoring import score_learning_session
from focusproof.runtime.evidence import Evidence, LearningGoal
from focusproof.runtime.observations import Observation


def general_goal(title: str, goal: str) -> LearningGoal:
    return LearningGoal(domain="general", title=title, goal=goal)


def text_evidence(evidence_id: str, text: str) -> Evidence:
    return Evidence(
        evidenceId=evidence_id,
        evidenceType="text",
        contentHash=f"sha256:{evidence_id}",
        textContent=text,
    )


def _goal() -> LearningGoal:
    return LearningGoal(
        domain="web3",
        title="Understand Monad transactions",
        goal="Explain how a transaction is submitted and confirmed",
        expectedOutput=None,
        plannedMinutes=120,
    )


def test_no_evidence_gets_low_score() -> None:
    result = score_learning_session(goal=_goal(), evidence=[], answers=[])

    assert result.score <= 20
    assert result.status == "InsufficientEvidence"
    assert result.findings


def test_generic_text_evidence_gets_weak_score() -> None:
    result = score_learning_session(
        goal=_goal(),
        evidence=[
            Evidence(
                evidenceId="ev_generic",
                evidenceType="text",
                contentHash="sha256:g",
                textContent="I studied a lot today and learned many things.",
            )
        ],
        answers=[],
    )

    assert result.score <= 40
    assert result.status == "WeakEvidence"
    assert result.findings[0].evidenceIds == ["ev_generic"]


def test_transaction_hash_without_explanation_does_not_get_high_score() -> None:
    result = score_learning_session(
        goal=_goal(),
        evidence=[
            Evidence(
                evidenceId="ev_tx",
                evidenceType="transaction",
                contentHash="sha256:tx",
                textContent="0x1234567890abcdef",
            )
        ],
        answers=[],
    )

    assert result.score <= 55
    assert result.status in {"NeedsMoreVerification", "WeakEvidence"}


def test_text_evidence_plus_answer_can_improve_score() -> None:
    result = score_learning_session(
        goal=_goal(),
        evidence=[
            Evidence(
                evidenceId="ev_text",
                evidenceType="text",
                contentHash="sha256:t",
                textContent=(
                    "A transaction includes nonce, gas, signature, sender, receiver, and data. "
                    "It is broadcast, executed, and confirmed in a block."
                ),
            )
        ],
        answers=["The nonce orders account transactions and gas limits execution cost."],
    )

    assert result.score >= 60
    assert result.status in {"LikelyLearning", "VerifiedLearning"}


def test_specific_non_web3_explanation_can_show_learning() -> None:
    goal = general_goal(
        "Understand photosynthesis",
        "Explain photosynthesis using a concrete example",
    )
    evidence = [
        text_evidence(
            "ev_photo",
            "Chlorophyll absorbs light; I compared a shaded leaf with a lit "
            "leaf and recorded the color change as a concrete example.",
        )
    ]
    result = score_learning_session(
        goal,
        evidence,
        ["The control isolates light as the changed variable."],
    )
    assert result.score >= 60
    assert result.status == "LikelyLearning"


def test_web3_keywords_alone_do_not_raise_general_understanding() -> None:
    goal = general_goal("Understand transactions", "Explain transaction ordering")
    evidence = [
        text_evidence(
            "ev_keywords",
            "nonce gas transaction block confirmation",
        )
    ]
    result = score_learning_session(goal, evidence, [])
    assert result.score < 60


def test_long_web3_vocabulary_list_does_not_raise_general_understanding() -> None:
    goal = general_goal("Understand controls", "Explain an experimental control")
    evidence = [
        text_evidence(
            "ev_long_keywords",
            "nonce gas signature sender receiver block confirmation transaction wallet "
            "chain network token",
        )
    ]
    result = score_learning_session(goal, evidence, [])
    assert result.score < 60


def test_observation_success_does_not_assign_final_learning() -> None:
    goal = general_goal(
        "Understand controls",
        "Explain why an experiment uses a control",
    )
    evidence = [
        text_evidence(
            "ev_control",
            "I compared two groups and changed one variable.",
        )
    ]
    observation = Observation(
        toolName="focusproof_text_evidence_verification",
        status="success",
        facts={"has_text": True, "word_count": 9},
        sourceRefs=["ev_control"],
    )
    result = score_learning_session(goal, evidence, [], [observation])
    assert result.status != "VerifiedLearning"


def test_url_only_evidence_with_specific_answer_is_not_generic_text() -> None:
    goal = general_goal(
        "Understand retry guidance",
        "Explain retry guidance from the referenced documentation",
    )
    evidence = [
        Evidence(
            evidenceId="ev_url",
            evidenceType="url",
            contentHash="sha256:url",
            sourceUrl="https://example.com/retry-guide",
        )
    ]
    observation = Observation(
        toolName="focusproof_url_evidence_verification",
        status="success",
        facts={"status_code": 200, "title": "Retry guide"},
        sourceRefs=["ev_url", "sha256:url", "https://example.com/retry-guide"],
    )

    result = score_learning_session(
        goal,
        evidence,
        ["Retry guidance uses bounded exponential delays to reduce contention."],
        [observation],
    )

    assert result.score >= 60
    assert result.status == "LikelyLearning"


def test_substantive_cjk_text_is_not_treated_as_generic() -> None:
    goal = general_goal("理解实验对照", "解释为什么实验需要对照组")
    evidence = [
        text_evidence(
            "ev_cjk",
            "我比较了光照组和遮光组，只改变光照条件，并记录叶片颜色变化来说明对照组的作用。",
        )
    ]

    result = score_learning_session(
        goal,
        evidence,
        ["对照组帮助排除其他变量，让观察到的差异能归因于光照。"],
    )

    assert result.score >= 60
    assert result.status == "LikelyLearning"
