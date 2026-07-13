from focusproof.domain.scoring import score_learning_session
from focusproof.runtime.evidence import Evidence, LearningGoal


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
