from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.base import Event as OpenHandsEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM

from focusproof.domain.scoring import score_learning_session
from focusproof.domain.scoring_inputs import LearningNarrativeProjector, narratives_as_evidence
from focusproof.media_projection.image_narrative_provider import (
    ImageNarrativeProvider,
    ImageVerificationCompletionPolicy,
)
from focusproof.openhands_runtime.result_extractor import (
    _RuntimeResultExtractor,
    _focusproof_observations,
    _project_safe_completed_review_lineage,
)
from focusproof.openhands_runtime.handle import ConversationHandle
from focusproof.openhands_runtime.tools.review_draft import ReviewDraftObservation
from focusproof.openhands_runtime.tools.verification import (
    EvidenceReferenceAction,
    VerificationObservation,
)
from focusproof.runtime.audit_projection import InMemoryAuditProjectionStore
from focusproof.runtime.evidence import Evidence, LearningGoal


@pytest.fixture
def official_handle_factory(
    tmp_path: Path,
) -> Iterator[Callable[[str, Sequence[OpenHandsEvent]], ConversationHandle]]:
    conversations: list[LocalConversation] = []

    def create_handle(
        session_id: str,
        native_events: Sequence[OpenHandsEvent],
    ) -> ConversationHandle:
        runtime_root = tmp_path / session_id
        conversation_id = uuid4()
        conversation = Conversation(
            agent=Agent(
                llm=TestLLM(model="test-model"),
                tools=[],
                include_default_tools=[],
            ),
            workspace=runtime_root / "workspace",
            persistence_dir=runtime_root / "persistence",
            conversation_id=conversation_id,
            max_iteration_per_run=1,
            visualizer=None,
            delete_on_close=True,
        )
        assert isinstance(conversation, LocalConversation)
        for event in native_events:
            conversation.state.append_event(event)
        conversations.append(conversation)
        return ConversationHandle(
            session_id=session_id,
            conversation=conversation,
            conversation_id=conversation_id,
            workspace_path=runtime_root / "workspace",
            persistence_path=runtime_root / "persistence",
            runtime_mode="openhands-local-scripted-test",
            toolset_version="test",
        )

    yield create_handle

    for conversation in reversed(conversations):
        conversation.close()


def test_image_review_uses_verified_facts_without_claiming_model_saw_pixels() -> None:
    evidence = [
        Evidence(
            evidenceId="ev_image",
            evidenceType="image/png",
            contentHash="sha256:image",
        )
    ]
    goal = LearningGoal(
        domain="web3",
        title="Understand Monad transactions",
        goal="Explain how a Monad transaction is submitted and confirmed",
    )
    now = datetime.now(UTC)
    native_observation = ObservationEvent(
        tool_name="focusproof_media_evidence_verification",
        tool_call_id="call_media",
        observation=VerificationObservation.from_text(
            "verified media facts",
            evidence_id="ev_image",
            capability="image",
            status="success",
            facts={
                "artifact_ref": "artifact://safe-image-ref",
                "media_type": "image/png",
                "normalized_sha256": "22" * 32,
                "byte_size": 1200,
                "width": 640,
                "height": 480,
                "learner_explanation": (
                    "The diagram shows a signed Monad transaction with nonce and gas, "
                    "then execution and confirmation in a block."
                ),
                "visual_facts": [
                    "A signed transaction contains a nonce.",
                    "A gas limit is visible before execution.",
                    "The result is confirmed in a block.",
                ],
            },
            weak_signals=[],
            source_refs=["ev_image", "artifact://safe-image-ref", "sha256:" + "22" * 32],
            verifier_version="1",
            started_at=now,
            completed_at=now,
        ),
        action_id="action_media",
    )

    observations = _focusproof_observations([native_observation])
    narratives = LearningNarrativeProjector(providers=(ImageNarrativeProvider(),)).project_all(
        evidence, observations
    )
    review = score_learning_session(
        goal,
        narratives_as_evidence(evidence, narratives),
        ["The nonce orders transactions and gas limits computation."],
        observations,
    )
    serialized = json.dumps(
        {
            "observations": [item.model_dump(mode="json") for item in observations],
            "narratives": [asdict(item) for item in narratives],
            "review": review.model_dump(mode="json"),
        },
        sort_keys=True,
    )

    assert review.status == "LikelyLearning"
    assert review.score >= 60
    assert len(narratives[0].consumed_fact_ids) == 3
    assert narratives[0].source_observation_event_id == str(native_observation.id)
    assert "artifact_resolving_llm" not in sys.modules
    assert "visioninspecttool" not in serialized.lower()
    assert "saw the image" not in serialized.lower()
    assert "/home/" not in serialized
    assert "object_key" not in serialized
    assert "api_key" not in serialized.lower()


def test_image_fact_lineage_reaches_score_and_review_audit(
    official_handle_factory: Callable[[str, Sequence[OpenHandsEvent]], ConversationHandle],
) -> None:
    now = datetime.now(UTC)
    media_tool_call = MessageToolCall(
        id="call_media_lineage",
        name="focusproof_media_evidence_verification",
        arguments='{"evidence_id":"ev_image"}',
        origin="completion",
    )
    media_action = ActionEvent(
        thought=[TextContent(text="Verify the image evidence")],
        action=EvidenceReferenceAction(evidence_id="ev_image"),
        tool_name=media_tool_call.name,
        tool_call_id=media_tool_call.id,
        tool_call=media_tool_call,
        llm_response_id="response_media_lineage",
    )
    media = ObservationEvent(
        tool_name="focusproof_media_evidence_verification",
        tool_call_id="call_media_lineage",
        observation=VerificationObservation.from_text(
            "verified media facts",
            evidence_id="ev_image",
            capability="image",
            status="success",
            facts={
                "learner_explanation": "The screenshot shows transaction progress.",
                "visual_facts": ["nonce visible", "gas visible", "block confirmed"],
            },
            weak_signals=[],
            source_refs=["ev_image"],
            verifier_version="1",
            started_at=now,
            completed_at=now,
        ),
        action_id=media_action.id,
    )
    draft = ObservationEvent(
        tool_name="focusproof_review_draft",
        tool_call_id="call_draft_lineage",
        observation=ReviewDraftObservation.from_text(
            "draft",
            accepted=True,
            draft_id="draft_lineage",
            credibility_findings=[],
            understanding_findings=[],
            contradictions=[],
            recommended_next_step="Continue.",
            confidence=0.8,
        ),
        action_id="action_draft_lineage",
    )
    handle = official_handle_factory(
        "sess_image_lineage",
        [media_action, media, draft],
    )
    audit = InMemoryAuditProjectionStore()
    result = _RuntimeResultExtractor(
        audit,
        narrative_providers=(ImageNarrativeProvider(),),
        completion_policies=(ImageVerificationCompletionPolicy(),),
    )._extract_managed(
        handle=handle,
        native_events=list(handle.conversation.state.events),
        goal=LearningGoal(domain="general", title="Inspect", goal="Explain image facts"),
        evidence=[
            Evidence(evidenceId="ev_image", evidenceType="image/png", contentHash="sha256:image")
        ],
        answers=[],
    )

    assert result.reviewStatus == "completed"
    score_event = audit.get_by_type("sess_image_lineage", "score.calculated")[0]
    review_event = audit.get_by_type("sess_image_lineage", "review.completed")[0]
    lineage = score_event.payload["narrativeLineage"][0]
    assert lineage["sourceObservationEventId"] == str(media.id)
    assert len(lineage["facts"]) == 3
    assert all("text" not in fact for fact in lineage["facts"])
    assert score_event.payload["sourceObservationEventId"] == str(draft.id)
    assert review_event.payload["sourceObservationEventId"] == str(draft.id)
    assert score_event.id == f"evt_score_{draft.id}"
    assert review_event.id == f"evt_review_{draft.id}"
    assert review_event.payload["scoreEventId"] == score_event.id
    assert review_event.payload["narrativeProjectionIds"] == [lineage["projectionId"]]
    safe_lineage = _project_safe_completed_review_lineage(
        audit.list("sess_image_lineage"),
        native_events=list(handle.conversation.state.events),
    )
    assert safe_lineage["sourceObservationEventIds"] == [str(media.id)]
    assert safe_lineage["score"] == score_event.id
    assert safe_lineage["review"] == review_event.id


def test_completed_review_fails_closed_without_three_native_visual_facts(
    official_handle_factory: Callable[[str, Sequence[OpenHandsEvent]], ConversationHandle],
) -> None:
    draft = ObservationEvent(
        tool_name="focusproof_review_draft",
        tool_call_id="call_draft",
        observation=ReviewDraftObservation.from_text(
            "draft",
            accepted=True,
            draft_id="draft_media",
            credibility_findings=["The learner supplied an image."],
            understanding_findings=["The answer mentions the image."],
            contradictions=[],
            recommended_next_step="Verify the pixels.",
            confidence=0.8,
        ),
        action_id="action_draft",
    )
    handle = official_handle_factory("sess_media_gate", [draft])
    result = _RuntimeResultExtractor(
        InMemoryAuditProjectionStore(),
        completion_policies=(ImageVerificationCompletionPolicy(),),
    )._extract_managed(
        handle=handle,
        native_events=list(handle.conversation.state.events),
        goal=LearningGoal(
            domain="general",
            title="Inspect an image",
            goal="Describe concrete visual details.",
        ),
        evidence=[
            Evidence(
                evidenceId="ev_image",
                evidenceType="image/png",
                contentHash="sha256:image",
                textContent="The image proves I studied.",
            )
        ],
        answers=["I saw the image and understand it."],
    )

    assert result.reviewStatus == "failed"
    assert result.reviewResult is None
    assert "visual" in (result.error or "").lower()


def _image_answer_event() -> MessageEvent:
    return MessageEvent(
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text=json.dumps({"kind": "answer"}))],
        ),
    )


def _media_verification_events(
    evidence_id: str,
    *,
    suffix: str,
    visual_facts: Sequence[str] = ("nonce visible", "gas visible", "block confirmed"),
) -> tuple[ActionEvent, ObservationEvent]:
    now = datetime.now(UTC)
    tool_call = MessageToolCall(
        id=f"call_media_{suffix}",
        name="focusproof_media_evidence_verification",
        arguments=json.dumps({"evidence_id": evidence_id}),
        origin="completion",
    )
    action = ActionEvent(
        thought=[TextContent(text="Verify the uploaded image evidence")],
        action=EvidenceReferenceAction(evidence_id=evidence_id),
        tool_name=tool_call.name,
        tool_call_id=tool_call.id,
        tool_call=tool_call,
        llm_response_id=f"response_media_{suffix}",
    )
    observation = ObservationEvent(
        tool_name=tool_call.name,
        tool_call_id=tool_call.id,
        observation=VerificationObservation.from_text(
            "verified media facts",
            evidence_id=evidence_id,
            capability="image",
            status="success",
            facts={
                "learner_explanation": "The screenshot shows a specific learning session.",
                "visual_facts": list(visual_facts),
            },
            weak_signals=[],
            source_refs=[evidence_id],
            verifier_version="1",
            started_at=now,
            completed_at=now,
        ),
        action_id=action.id,
    )
    return action, observation


def _accepted_review_draft(*, suffix: str) -> ObservationEvent:
    return ObservationEvent(
        tool_name="focusproof_review_draft",
        tool_call_id=f"call_draft_{suffix}",
        observation=ReviewDraftObservation.from_text(
            "draft",
            accepted=True,
            draft_id=f"draft_{suffix}",
            credibility_findings=[],
            understanding_findings=[],
            contradictions=[],
            recommended_next_step="Continue.",
            confidence=0.8,
        ),
        action_id=f"action_draft_{suffix}",
    )


def test_completed_review_uses_current_round_image_observation_even_if_it_precedes_answer(
    official_handle_factory: Callable[[str, Sequence[OpenHandsEvent]], ConversationHandle],
) -> None:
    old_media_action, old_media = _media_verification_events("ev_image", suffix="old")
    old_draft = _accepted_review_draft(suffix="old")
    current_media_action, current_media = _media_verification_events(
        "ev_image",
        suffix="current",
        visual_facts=("topic title visible", "goal text visible", "evidence card visible"),
    )
    answer = _image_answer_event()
    current_draft = _accepted_review_draft(suffix="current")
    handle = official_handle_factory(
        "sess_image_current_round",
        [
            old_media_action,
            old_media,
            old_draft,
            current_media_action,
            current_media,
            answer,
            current_draft,
        ],
    )
    audit = InMemoryAuditProjectionStore()

    result = _RuntimeResultExtractor(
        audit,
        narrative_providers=(ImageNarrativeProvider(),),
        completion_policies=(ImageVerificationCompletionPolicy(),),
    )._extract_managed(
        handle=handle,
        native_events=list(handle.conversation.state.events),
        goal=LearningGoal(domain="general", title="Inspect", goal="Explain image facts"),
        evidence=[
            Evidence(evidenceId="ev_image", evidenceType="image/png", contentHash="sha256:image")
        ],
        answers=["The topic and goal are visible in the uploaded image."],
    )

    assert result.reviewStatus == "completed"
    score_event = audit.get_by_type("sess_image_current_round", "score.calculated")[0]
    lineage = score_event.payload["narrativeLineage"][0]
    assert lineage["sourceObservationEventId"] == str(current_media.id)
    assert score_event.payload["sourceObservationEventId"] == str(current_draft.id)


def test_completed_review_does_not_reuse_old_round_image_observation_after_prior_completion(
    official_handle_factory: Callable[[str, Sequence[OpenHandsEvent]], ConversationHandle],
) -> None:
    old_media_action, old_media = _media_verification_events("ev_image", suffix="old")
    old_draft = _accepted_review_draft(suffix="old")
    answer = _image_answer_event()
    current_draft = _accepted_review_draft(suffix="current")
    handle = official_handle_factory(
        "sess_image_old_round",
        [old_media_action, old_media, old_draft, answer, current_draft],
    )

    result = _RuntimeResultExtractor(
        InMemoryAuditProjectionStore(),
        completion_policies=(ImageVerificationCompletionPolicy(),),
    )._extract_managed(
        handle=handle,
        native_events=list(handle.conversation.state.events),
        goal=LearningGoal(domain="general", title="Inspect", goal="Explain image facts"),
        evidence=[
            Evidence(evidenceId="ev_image", evidenceType="image/png", contentHash="sha256:image")
        ],
        answers=["I answered a new question, but no new image verification happened."],
    )

    assert result.reviewStatus == "failed"
    assert "visual" in (result.error or "").lower()
