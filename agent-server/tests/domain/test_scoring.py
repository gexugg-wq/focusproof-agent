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
        title="Understand payment transactions",
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


def test_generic_text_does_not_poison_url_evidence_and_specific_answer() -> None:
    goal = general_goal(
        "Understand retry guidance",
        "Explain retry guidance from the referenced documentation",
    )
    evidence = [
        text_evidence("ev_note", "I learned a lot."),
        Evidence(
            evidenceId="ev_url",
            evidenceType="url",
            contentHash="sha256:url",
            sourceUrl="https://example.com/retry-guide",
        ),
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


def test_trivial_answer_does_not_elevate_generic_goal_aligned_text() -> None:
    goal = general_goal(
        "Understand retry guidance",
        "Explain retry guidance from the referenced documentation",
    )
    evidence = [
        text_evidence(
            "ev_generic",
            "I learned a lot about retry guidance and referenced documentation today.",
        )
    ]

    result = score_learning_session(goal, evidence, ["yes"])

    assert result.score < 60
    assert result.status == "WeakEvidence"


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


def _goal_copy_warning_messages(result: object) -> list[str]:
    findings = getattr(result, "findings")
    return [
        finding.message
        for finding in findings
        if finding.severity == "warning" and "restates the learning goal" in finding.message
    ]


def test_exact_english_goal_copy_is_weak_evidence() -> None:
    copied = "Explain how an append-only event log rebuilds application state."
    result = score_learning_session(
        general_goal("Understand event replay", copied),
        [text_evidence("ev_copy_en", copied)],
        [],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)
    assert result.findings[0].evidenceIds == ["ev_copy_en"]


def test_goal_copy_normalizes_unicode_case_whitespace_and_punctuation() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    formatted_copy = (
        "  ＥＸＰＬＡＩＮ, HOW   an APPEND—ONLY event log\nrebuilds application state！！！  "
    )
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [text_evidence("ev_copy_normalized", formatted_copy)],
        [],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_exact_chinese_goal_copy_is_weak_evidence() -> None:
    copied = "解释为什么实验需要对照组，并说明如何排除其他变量。"
    result = score_learning_session(
        general_goal("理解实验对照", copied),
        [text_evidence("ev_copy_zh", copied)],
        [],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_light_goal_rewording_without_new_information_is_weak_evidence() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    rewording = "Describe how an append-only event log rebuilds application state."
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [text_evidence("ev_copy_reworded", rewording)],
        [],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_shared_domain_terms_with_independent_explanation_are_not_goal_copy() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    explanation = (
        "Replay starts from an empty reducer state and applies each stored event in order. "
        "A deposit increments the balance while a later withdrawal decrements it, so the "
        "same final view can be reproduced without overwriting history."
    )
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [text_evidence("ev_independent_explanation", explanation)],
        [],
    )

    assert result.score >= 60
    assert result.status == "LikelyLearning"
    assert not _goal_copy_warning_messages(result)


def test_quoted_goal_followed_by_concrete_example_is_not_goal_copy() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    evidence = (
        f"{goal} For example, replay begins with a zero balance, then a deposit event adds "
        "ten and a withdrawal event subtracts three, producing the same final balance of seven."
    )
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [text_evidence("ev_goal_plus_example", evidence)],
        [],
    )

    assert result.score >= 60
    assert result.status == "LikelyLearning"
    assert not _goal_copy_warning_messages(result)


def test_goal_copy_plus_independent_strong_evidence_is_not_globally_weakened() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    independent = (
        "I rebuilt a balance by reducing ordered deposit and withdrawal events from zero. "
        "Deleting the cached view did not lose the result because replay recomputed it from "
        "the retained history."
    )
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [
            text_evidence("ev_copy_with_peer", goal),
            text_evidence("ev_independent_peer", independent),
        ],
        [],
    )

    assert result.score >= 60
    assert result.status == "LikelyLearning"
    assert not _goal_copy_warning_messages(result)


def test_goal_copy_plus_specific_answer_improves_support_but_remains_weak() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    answer = (
        "Starting with an empty balance, replay applies each deposit and withdrawal once in "
        "sequence; retaining those events lets the same view be rebuilt after a cache loss."
    )
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [text_evidence("ev_copy_with_answer", goal)],
        [answer],
    )

    assert 35 < result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_goal_copy_plus_answer_copying_goal_remains_weak() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [text_evidence("ev_copy_with_copied_answer", goal)],
        ["EXPLAIN how an append only event log rebuilds application state!"],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_goal_copy_plus_long_unrelated_answer_remains_weak() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    unrelated_answer = (
        "French impressionist painters often used broken brushwork and outdoor light to show "
        "how colors change across a landscape during different parts of the day."
    )
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [text_evidence("ev_copy_with_unrelated_answer", goal)],
        [unrelated_answer],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_goal_copy_plus_ordinary_text_verification_remains_weak() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    observation = Observation(
        toolName="focusproof_text_evidence_verification",
        status="success",
        facts={"has_text": True, "word_count": 10, "content_hash": "sha256:copy"},
        sourceRefs=["ev_copy_with_text_check", "sha256:copy"],
    )
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [text_evidence("ev_copy_with_text_check", goal)],
        [],
        [observation],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_goal_copy_plus_unrelated_success_observation_remains_weak() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    observation = Observation(
        toolName="focusproof_url_evidence_verification",
        status="success",
        facts={"status_code": 200, "title": "Unrelated reachable page"},
        sourceRefs=["ev_not_submitted", "https://example.com/unrelated"],
    )
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [text_evidence("ev_copy_with_unrelated_observation", goal)],
        [],
        [observation],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_goal_copy_plus_generic_filler_answer_remains_weak() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [text_evidence("ev_copy_with_filler", goal)],
        ["I learned a lot and gained many useful insights about this topic today."],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_goal_copy_plus_detailed_unrelated_evidence_remains_weak() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    unrelated = (
        "Impressionist painters placed complementary colors beside each other and used short "
        "visible brush strokes to represent changing outdoor light across a landscape."
    )
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [
            text_evidence("ev_copy_with_unrelated_peer", goal),
            text_evidence("ev_detailed_unrelated_peer", unrelated),
        ],
        [],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_goal_copy_plus_related_specific_evidence_uses_normal_scoring() -> None:
    goal = "Explain how an append-only event log rebuilds application state."
    related = (
        "Event replay starts from an empty state and applies each stored event in order. "
        "For example, a deposit adds ten to a zero balance and a withdrawal subtracts three, "
        "so rebuilding from the retained history deterministically returns seven."
    )
    result = score_learning_session(
        general_goal("Understand event replay", goal),
        [
            text_evidence("ev_copy_with_related_peer", goal),
            text_evidence("ev_specific_related_peer", related),
        ],
        [],
    )

    assert result.score >= 60
    assert result.status == "LikelyLearning"
    assert not _goal_copy_warning_messages(result)


def test_chinese_goal_copy_plus_specific_correct_answer_stays_below_likely() -> None:
    goal = "解释为什么实验需要对照组，并说明如何排除其他变量。"
    answer = (
        "对照组保持光照以外的条件一致；例如只遮住一组叶片，再比较颜色变化，"
        "就能把观察到的差异归因于光照而不是水分或温度。"
    )
    result = score_learning_session(
        general_goal("理解实验对照", goal),
        [text_evidence("ev_copy_zh_with_answer", goal)],
        [answer],
    )

    assert 35 < result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)


def test_chinese_goal_copy_plus_generic_filler_answer_remains_weak() -> None:
    goal = "解释为什么实验需要对照组，并说明如何排除其他变量。"
    result = score_learning_session(
        general_goal("理解实验对照", goal),
        [text_evidence("ev_copy_zh_with_filler", goal)],
        ["我学到了很多内容，感觉收获很大，以后还会继续认真学习这个主题。"],
    )

    assert result.score < 60
    assert result.status == "WeakEvidence"
    assert _goal_copy_warning_messages(result)
