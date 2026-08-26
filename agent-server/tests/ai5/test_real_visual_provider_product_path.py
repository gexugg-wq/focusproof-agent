from __future__ import annotations

import json
import re
from contextlib import contextmanager
from hashlib import sha256
from inspect import signature
from pathlib import Path
from typing import Any

import pytest
from openhands.sdk import Conversation
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.llm import ImageContent, Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM

from focusproof.openhands_runtime import result_extractor
from focusproof.openhands_runtime.handle import ConversationHandle
from focusproof.openhands_runtime.manager import ConversationManager
from scripts import run_real_visual_provider_gate as gate


class WrappedNativeEvents(list[Any]):
    """Attacker-controlled container that must never become a provenance input."""


class ProductPathTestLLM(TestLLM):
    def vision_is_active(self) -> bool:
        return True

    """Official SDK TestLLM subclass that scripts the real media/review tool path."""

    def completion(self, messages: list[Message], tools: Any = None, **kwargs: Any) -> Any:
        serialized = "\n".join(message.model_dump_json() for message in messages)
        if self._scripted_responses:
            return super().completion(messages, tools=tools, **kwargs)
        if tools is None and any(
            isinstance(content, ImageContent) for message in messages for content in message.content
        ):
            self._scripted_responses.append(
                Message(
                    role="assistant",
                    content=[
                        TextContent(
                            text=json.dumps(
                                {
                                    "visual_facts": [
                                        "A browser capability is selected.",
                                        "A success state is visible.",
                                        "The evidence panel shows completion.",
                                    ]
                                }
                            )
                        )
                    ],
                )
            )
        else:
            agent_calls = int(self.__dict__.get("product_path_agent_calls", 0))
            self.__dict__["product_path_agent_calls"] = agent_calls + 1
        if tools is not None and agent_calls == 0:
            evidence_ids = re.findall(
                r"(?:evidenceId|evidence_id|Evidence ID)[^A-Za-z0-9_-]+([A-Za-z0-9_-]+)",
                serialized,
            )
            if not evidence_ids:
                evidence_ids = re.findall(r"\bev_[A-Za-z0-9_-]+\b", serialized)
            assert evidence_ids, serialized[-2000:]
            self._scripted_responses.append(
                Message(
                    role="assistant",
                    content=[TextContent(text="Verify the uploaded image evidence.")],
                    tool_calls=[
                        MessageToolCall(
                            id="call_media_product_path",
                            name="focusproof_media_evidence_verification",
                            arguments=json.dumps({"evidence_id": evidence_ids[-1]}),
                            origin="completion",
                        )
                    ],
                )
            )
            self._scripted_responses.extend(
                [
                    Message(
                        role="assistant",
                        content=[
                            TextContent(
                                text=json.dumps(
                                    {
                                        "visual_facts": [
                                            "A browser capability is selected.",
                                            "A success state is visible.",
                                            "The evidence panel shows completion.",
                                        ]
                                    }
                                )
                            )
                        ],
                    ),
                    Message(
                        role="assistant",
                        content=[TextContent(text="Submit the grounded review draft.")],
                        tool_calls=[
                            MessageToolCall(
                                id="call_draft_product_path",
                                name="focusproof_review_draft",
                                arguments=json.dumps(
                                    {
                                        "credibility_findings": [
                                            "Three receipt-backed visual facts are present."
                                        ],
                                        "understanding_findings": [
                                            "The explanation is grounded in visible state."
                                        ],
                                        "contradictions": [],
                                        "recommended_next_step": "Repeat with a second screenshot.",
                                        "confidence": 0.9,
                                    }
                                ),
                                origin="completion",
                            )
                        ],
                    ),
                    Message(
                        role="assistant", content=[TextContent(text="Review draft submitted.")]
                    ),
                ]
            )
        elif tools is not None and agent_calls == 1:
            self._scripted_responses.append(
                Message(
                    role="assistant",
                    content=[TextContent(text="Submit the grounded review draft.")],
                    tool_calls=[
                        MessageToolCall(
                            id="call_draft_product_path",
                            name="focusproof_review_draft",
                            arguments=json.dumps(
                                {
                                    "credibility_findings": [
                                        "Three receipt-backed visual facts are present."
                                    ],
                                    "understanding_findings": [
                                        "The explanation is grounded in visible state."
                                    ],
                                    "contradictions": [],
                                    "recommended_next_step": "Repeat with a second screenshot.",
                                    "confidence": 0.9,
                                }
                            ),
                            origin="completion",
                        )
                    ],
                )
            )
        else:
            self._scripted_responses.append(
                Message(role="assistant", content=[TextContent(text="Review draft submitted.")])
            )
        return super().completion(messages, tools=tools, **kwargs)


def test_product_path_pass_report_is_observed_validated_and_auditable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    llms: list[ProductPathTestLLM] = []
    applications: list[Any] = []
    actual_audit_events: list[Any] = []
    actual_native_events: list[Any] = []

    def llm_factory(session_id: str) -> ProductPathTestLLM:
        del session_id
        llm = ProductPathTestLLM(model="test-model")
        llms.append(llm)
        return llm

    original_create_app = gate.create_app

    def create_captured_app(*args: Any, **kwargs: Any) -> Any:
        application = original_create_app(*args, **kwargs)
        applications.append(application)
        return application

    original_safe_summary = gate._safe_eventlog_summary

    def capture_product_audit(*args: Any, **kwargs: Any) -> dict[str, object]:
        application = applications[-1]
        manager = application.state.conversation_manager
        session_ids = list(manager._handles)
        assert len(session_ids) == 1
        actual_audit_events.extend(application.state.audit_projection_store.list(session_ids[0]))
        assert "conversation_handle" not in kwargs
        assert "audit_events" not in kwargs
        assert isinstance(manager.get(session_ids[0]), ConversationHandle)
        native_events = args[0]
        assert isinstance(native_events, list)
        actual_native_events.extend(native_events)
        return original_safe_summary(*args, **kwargs)

    monkeypatch.setattr(gate, "create_app", create_captured_app)
    monkeypatch.setattr(gate, "_safe_eventlog_summary", capture_product_audit)

    with gate._temporary_environment(
        {
            "FOCUSPROOF_PROFILE": "local-dev",
            "FOCUSPROOF_MEDIA_ENABLED": "true",
            "FOCUSPROOF_MEDIA_SCANNER_MODE": "fake-clean",
            "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "false",
        }
    ):
        try:
            result = gate._run_product_chain(
                gate.CANONICAL_IMAGE_PATH,
                "openai",
                "qwen3.7-plus",
                "fake-clean",
                llm_factory=llm_factory,
                allow_test_llm=True,
            )
        except gate.GateFailed as exc:
            call_counts = [llm._call_count for llm in llms]
            agent_counts = [llm.__dict__.get("product_path_agent_calls") for llm in llms]
            raise AssertionError(
                f"product path failed after calls={call_counts} agent_calls={agent_counts}"
            ) from exc
    report_path = tmp_path / "product-path.json"
    gate._publish_report_pair(
        report_path,
        result["report"],
        diagnostics=result["diagnostics"],
        eventlog_summary=result["eventlogSummary"],
    )
    report = json.loads(report_path.read_text())

    assert llms and isinstance(llms[0], TestLLM)
    assert report["checks"]["productionLlmUsed"] is False
    assert report["diagnostics"]["providerAttempted"] is True
    assert report["diagnostics"]["responseReceived"] is True
    assert report["diagnostics"]["completionSucceeded"] is True
    assert report["diagnostics"]["transportOutcome"] == "received"
    assert report["diagnostics"]["visualFactsCount"] >= 3
    assert report["diagnostics"]["visualProvider"] == {
        "provider": "openai",
        "model": "qwen3.7-plus",
    }
    assert report["diagnostics"]["reviewStatus"] == "completed"
    assert report["diagnostics"]["visualFactsParsed"] is True
    assert report["eventlogSummary"]["eventCounts"]["observationCount"] > 0
    assert report["eventlogSummary"]["schemaVersion"] == "3.0"
    assert report["eventlogSummary"]["consumedFactIds"]
    assert set(report["eventlogSummary"]["consumedFactIds"]) == set(
        report["eventlogSummary"]["factIds"]
    )
    assert len(report["eventlogSummary"]["factIds"]) >= 3
    assert report["eventlogSummary"]["source"] != "observation_missing"
    score_event = next(event for event in actual_audit_events if event.type == "score.calculated")
    review_event = next(event for event in actual_audit_events if event.type == "review.completed")
    safe_native_events = gate._safe_native_event_entries(actual_native_events)
    official_observation_ids = {
        str(event["id"])
        for event in safe_native_events
        if event.get("eventType") == "ObservationEvent"
    }
    narrative_lineage = score_event.payload["narrativeLineage"]
    assert isinstance(narrative_lineage, list) and len(narrative_lineage) == 1
    narrative = narrative_lineage[0]
    actual_fact_ids = [fact["factId"] for fact in narrative["facts"]]
    assert {
        "projection": report["eventlogSummary"]["projection"],
        "source": report["eventlogSummary"]["source"],
        "score": report["eventlogSummary"]["score"],
        "review": report["eventlogSummary"]["review"],
        "factIds": report["eventlogSummary"]["factIds"],
        "consumedFactIds": report["eventlogSummary"]["consumedFactIds"],
    } == {
        "projection": narrative["projectionId"],
        "source": narrative["sourceObservationEventId"],
        "score": score_event.id,
        "review": review_event.id,
        "factIds": actual_fact_ids,
        "consumedFactIds": score_event.payload["consumedFactIds"],
    }
    assert report["eventlogSummary"]["review_score"] == review_event.payload["scoreEventId"]
    assert set(report["eventlogSummary"]["sourceObservationEventIds"]).issubset(
        official_observation_ids
    )
    assert score_event.payload["sourceObservationEventId"] in official_observation_ids
    assert review_event.payload["sourceObservationEventId"] in official_observation_ids
    assert (
        score_event.payload["sourceObservationEventId"]
        == review_event.payload["sourceObservationEventId"]
    )
    assert score_event.id == f"evt_score_{review_event.payload['sourceObservationEventId']}"
    assert review_event.id == f"evt_review_{review_event.payload['sourceObservationEventId']}"
    assert (
        report["eventlogSummary"]["review_projection"]
        == review_event.payload["narrativeProjectionIds"][0]
    )
    serialized = json.dumps(report).lower()
    assert "data:image" not in serialized
    assert "base64," not in serialized
    assert "api_key" not in serialized


def test_product_lineage_has_no_external_handle_or_event_list_entry_point() -> None:
    assert not hasattr(result_extractor, "RuntimeResultExtractor")
    assert not hasattr(result_extractor, "project_safe_completed_review_lineage")
    assert list(
        signature(ConversationManager.project_safe_completed_review_lineage).parameters
    ) == ["self", "session_id"]


def test_product_path_zero_attempt_unknown_transport_cannot_publish_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_run_product_chain = gate._run_product_chain

    def llm_factory(session_id: str) -> ProductPathTestLLM:
        del session_id
        return ProductPathTestLLM(model="test-model")

    def run_with_product_test_llm(
        image_path: Path,
        provider: str,
        model: str,
        scanner_mode: str,
    ) -> dict[str, Any]:
        return original_run_product_chain(
            image_path,
            provider,
            model,
            scanner_mode,
            llm_factory=llm_factory,
            allow_test_llm=True,
        )

    @contextmanager
    def zero_attempt_observer(
        llm: object,
        observer: gate.CompletionObserver | None = None,
    ) -> Any:
        del llm, observer
        yield {
            "attempts": 0,
            "responseReceived": True,
            "completionSucceeded": True,
        }

    monkeypatch.setattr(gate, "_resolve_image", lambda path: gate.CANONICAL_IMAGE_PATH)
    monkeypatch.setattr(
        gate,
        "_provider_environment",
        lambda *args: (
            {
                "FOCUSPROOF_PROFILE": "local-dev",
                "FOCUSPROOF_MEDIA_ENABLED": "true",
                "FOCUSPROOF_MEDIA_SCANNER_MODE": "fake-clean",
                "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "false",
                "FOCUSPROOF_LLM_API_KEY": "safe-test-key",
            },
            "openai/qwen3.7-plus",
        ),
    )
    monkeypatch.setattr(gate, "_run_product_chain", run_with_product_test_llm)
    monkeypatch.setattr(gate, "_observe_completion_boundary", zero_attempt_observer)
    report_path = tmp_path / "zero-attempt.json"

    exit_code = gate.main(
        [
            "--execute-real-provider",
            "--report",
            str(report_path),
            "--image",
            str(gate.CANONICAL_IMAGE_PATH),
            "--provider",
            "openai",
            "--model",
            "qwen3.7-plus",
            "--scanner-mode",
            "fake-clean",
        ]
    )
    published = json.loads(report_path.read_text())

    assert exit_code != 0
    assert published["status"] != "PASS"
    assert published["diagnostics"]["providerAttempted"] is False
    assert published["diagnostics"]["transportOutcome"] == "unknown"


@pytest.mark.parametrize(
    "mutation",
    [
        "nonexistent_source_observation",
        "action_event_as_source",
        "review_draft_as_narrative_source",
        "projection_identity",
        "review_event_id",
        "coordinated_fact_ids",
        "fact_reorder",
        "fact_text",
        "fact_type",
        "fact_add",
        "fact_delete",
        "cross_session_observation",
        "paired_cross_session_events",
    ],
)
def test_product_path_tampered_real_audit_lineage_cannot_publish_pass(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    original_run_product_chain = gate._run_product_chain
    original_safe_summary = gate._safe_eventlog_summary
    original_create_app = gate.create_app
    applications: list[Any] = []
    foreign_native_events: list[Any] = []
    forged_attack_inputs: list[
        tuple[ConversationHandle, WrappedNativeEvents, ConversationHandle, str]
    ] = []

    def create_captured_app(*args: Any, **kwargs: Any) -> Any:
        application = original_create_app(*args, **kwargs)
        applications.append(application)
        return application

    def llm_factory(session_id: str) -> ProductPathTestLLM:
        del session_id
        return ProductPathTestLLM(model="test-model")

    def run_with_product_test_llm(
        image_path: Path,
        provider: str,
        model: str,
        scanner_mode: str,
    ) -> dict[str, Any]:
        return original_run_product_chain(
            image_path,
            provider,
            model,
            scanner_mode,
            llm_factory=llm_factory,
            allow_test_llm=True,
        )

    def tamper_real_audit_events(*args: Any, **kwargs: Any) -> dict[str, object]:
        native_events = args[0]
        safe_native_events = gate._safe_native_event_entries(native_events)
        application = applications[-1]
        manager = application.state.conversation_manager
        session_ids = list(manager._handles)
        assert len(session_ids) == 1
        session_id = session_ids[0]
        conversation_handle = manager.get(session_id)
        official_native_events = list(conversation_handle.conversation.state.events)

        def use_forged_same_id_handle(candidate_events: list[Any]) -> None:
            runtime_root = tmp_path / f"forged-{mutation}"
            forged_conversation = Conversation(
                agent=conversation_handle.conversation.agent,
                workspace=runtime_root / "workspace",
                persistence_dir=runtime_root / "persistence",
                conversation_id=conversation_handle.conversation_id,
                max_iteration_per_run=1,
                visualizer=None,
                delete_on_close=True,
            )
            assert isinstance(forged_conversation, LocalConversation)
            for event in candidate_events:
                forged_conversation.state.append_event(event.model_copy(update={"parent_id": None}))
            request.addfinalizer(forged_conversation.close)
            forged_handle = conversation_handle.model_copy(
                update={"conversation": forged_conversation}
            )
            assert forged_handle.conversation is not conversation_handle.conversation
            assert forged_handle.conversation.state.id == conversation_handle.conversation_id
            wrapped = WrappedNativeEvents(candidate_events)
            forged_attack_inputs.append((forged_handle, wrapped, conversation_handle, session_id))

        audit_store = application.state.audit_projection_store
        original_audit_list = audit_store.list
        audit_events = original_audit_list(session_id)
        assert isinstance(native_events, list)
        assert isinstance(official_native_events, list)
        assert isinstance(audit_events, list)
        mutated = [event.model_copy(deep=True) for event in audit_events]
        score_event = next(event for event in mutated if event.type == "score.calculated")
        review_index = next(
            index for index, event in enumerate(mutated) if event.type == "review.completed"
        )
        review_event = mutated[review_index]
        lineage = score_event.payload["narrativeLineage"]
        assert isinstance(lineage, list) and len(lineage) == 1
        item = lineage[0]
        assert isinstance(item, dict)
        facts = item["facts"]
        assert isinstance(facts, list) and len(facts) >= 3

        def replace_fact_lineage(replacement_facts: list[dict[str, object]]) -> None:
            fact_ids = [fact["factId"] for fact in replacement_facts]
            assert all(isinstance(fact_id, str) for fact_id in fact_ids)
            item["facts"] = replacement_facts
            item["consumedFactIds"] = fact_ids
            score_event.payload["consumedFactIds"] = sorted(fact_ids)
            evidence_id = item["evidenceId"]
            source_id = item["sourceObservationEventId"]
            assert isinstance(evidence_id, str) and isinstance(source_id, str)
            projection_id = (
                "projection_"
                + sha256(
                    f"{evidence_id}\n{source_id}\n".encode() + "\n".join(fact_ids).encode()
                ).hexdigest()
            )
            item["projectionId"] = projection_id
            review_event.payload["narrativeProjectionIds"] = [projection_id]

        if mutation == "review_event_id":
            mutated[review_index] = review_event.model_copy(
                update={"id": "evt_review_tampered_round3"}
            )
        elif mutation == "projection_identity":
            item["projectionId"] = "projection_tampered_round3"
            review_event.payload["narrativeProjectionIds"] = ["projection_tampered_round3"]
        elif mutation == "coordinated_fact_ids":
            replacement = [dict(fact) for fact in facts]
            for index, fact in enumerate(replacement):
                fact["factId"] = f"fact_round4_coordinated_{index}"
            replace_fact_lineage(replacement)
        elif mutation == "fact_reorder":
            replace_fact_lineage([dict(fact) for fact in reversed(facts)])
        elif mutation == "fact_text":
            image_index = next(
                index
                for index, event in enumerate(official_native_events)
                if getattr(getattr(event, "observation", None), "capability", None) == "image"
                and getattr(getattr(event, "observation", None), "status", None) == "success"
            )
            cloned_native = list(official_native_events)
            cloned_native[image_index] = official_native_events[image_index].model_copy(deep=True)
            image_observation = cloned_native[image_index].observation
            visual_facts = image_observation.facts["visual_facts"]
            assert isinstance(visual_facts, list) and visual_facts
            visual_facts[0] = "A different browser state is visible."
            normalized = [" ".join(fact.split()) for fact in visual_facts]
            source_id = str(cloned_native[image_index].id)
            evidence_id = image_observation.evidence_id
            item["evidenceId"] = evidence_id
            item["sourceObservationEventId"] = source_id
            replacement = [
                {
                    "factId": "fact_"
                    + sha256(f"{evidence_id}\n{source_id}\n{index}\n{fact}".encode()).hexdigest(),
                    "factType": "visual_text",
                    "textDigest": sha256(fact.encode()).hexdigest(),
                    "redaction": {"textPersisted": False},
                }
                for index, fact in enumerate(normalized)
            ]
            replace_fact_lineage(replacement)
            use_forged_same_id_handle(cloned_native)
        elif mutation == "fact_type":
            replacement = [dict(fact) for fact in facts]
            replacement[0]["factType"] = "attacker_controlled"
            replace_fact_lineage(replacement)
        elif mutation == "fact_add":
            replacement = [dict(fact) for fact in facts]
            replacement.append(
                {
                    "factId": "fact_round4_added",
                    "factType": "visual_text",
                    "textDigest": "f" * 64,
                    "redaction": {"textPersisted": False},
                }
            )
            replace_fact_lineage(replacement)
        elif mutation == "fact_delete":
            replace_fact_lineage([dict(fact) for fact in facts[:-1]])
        elif mutation in {
            "cross_session_observation",
            "paired_cross_session_events",
        }:
            foreign_source = next(
                event
                for event in foreign_native_events
                if getattr(getattr(event, "observation", None), "capability", None) == "image"
                and getattr(getattr(event, "observation", None), "status", None) == "success"
            )
            foreign_observation = foreign_source.observation
            foreign_visual_facts = foreign_observation.facts["visual_facts"]
            assert isinstance(foreign_visual_facts, list)
            normalized = [" ".join(fact.split()) for fact in foreign_visual_facts]
            foreign_source_id = str(foreign_source.id)
            item["evidenceId"] = foreign_observation.evidence_id
            item["sourceObservationEventId"] = foreign_source_id
            replacement = [
                {
                    "factId": "fact_"
                    + sha256(
                        f"{foreign_observation.evidence_id}\n{foreign_source_id}\n{index}\n{fact}".encode()
                    ).hexdigest(),
                    "factType": "visual_text",
                    "textDigest": sha256(fact.encode()).hexdigest(),
                    "redaction": {"textPersisted": False},
                }
                for index, fact in enumerate(normalized)
            ]
            replace_fact_lineage(replacement)
            injected_events = [foreign_source]
            if mutation == "paired_cross_session_events":
                foreign_action = next(
                    event
                    for event in foreign_native_events
                    if str(event.id) == str(foreign_source.action_id)
                )
                injected_events.insert(0, foreign_action)
            use_forged_same_id_handle([*official_native_events, *injected_events])
        else:
            if mutation == "nonexistent_source_observation":
                source_id = "obs_tampered_round3_missing"
                assert source_id not in {str(event.get("id")) for event in safe_native_events}
            elif mutation == "action_event_as_source":
                source_id = next(
                    str(event["id"])
                    for event in safe_native_events
                    if event.get("eventType") == "ActionEvent"
                )
            else:
                source_id = next(
                    str(event["id"])
                    for event in safe_native_events
                    if event.get("eventType") == "ObservationEvent"
                    and event.get("toolName") == "focusproof_review_draft"
                )
            score_event.payload["sourceObservationEventId"] = source_id
            review_event.payload["sourceObservationEventId"] = source_id
            for item in lineage:
                assert isinstance(item, dict)
                item["sourceObservationEventId"] = source_id

        def list_tampered_audit_events(requested_session_id: str) -> list[Any]:
            if requested_session_id == session_id:
                return mutated
            return original_audit_list(requested_session_id)

        monkeypatch.setattr(audit_store, "list", list_tampered_audit_events)
        return original_safe_summary(*args, **kwargs)

    monkeypatch.setattr(gate, "create_app", create_captured_app)
    monkeypatch.setattr(gate, "_resolve_image", lambda path: gate.CANONICAL_IMAGE_PATH)
    monkeypatch.setattr(
        gate,
        "_provider_environment",
        lambda *args: (
            {
                "FOCUSPROOF_PROFILE": "local-dev",
                "FOCUSPROOF_MEDIA_ENABLED": "true",
                "FOCUSPROOF_MEDIA_SCANNER_MODE": "fake-clean",
                "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "false",
                "FOCUSPROOF_LLM_API_KEY": "safe-test-key",
            },
            "openai/qwen3.7-plus",
        ),
    )
    monkeypatch.setattr(gate, "_run_product_chain", run_with_product_test_llm)
    if mutation in {"cross_session_observation", "paired_cross_session_events"}:

        def capture_foreign_run(*args: Any, **kwargs: Any) -> dict[str, object]:
            application = applications[-1]
            manager = application.state.conversation_manager
            session_ids = list(manager._handles)
            assert len(session_ids) == 1
            native = list(manager.get(session_ids[0]).conversation.state.events)
            assert isinstance(native, list)
            foreign_native_events.extend(native)
            return original_safe_summary(*args, **kwargs)

        monkeypatch.setattr(gate, "_safe_eventlog_summary", capture_foreign_run)
        with gate._temporary_environment(
            {
                "FOCUSPROOF_PROFILE": "local-dev",
                "FOCUSPROOF_MEDIA_ENABLED": "true",
                "FOCUSPROOF_MEDIA_SCANNER_MODE": "fake-clean",
                "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "false",
            }
        ):
            original_run_product_chain(
                gate.CANONICAL_IMAGE_PATH,
                "openai",
                "qwen3.7-plus",
                "fake-clean",
                llm_factory=llm_factory,
                allow_test_llm=True,
            )
        assert foreign_native_events
    monkeypatch.setattr(gate, "_safe_eventlog_summary", tamper_real_audit_events)
    report_path = tmp_path / f"tampered-real-audit-{mutation}.json"

    exit_code = gate.main(
        [
            "--execute-real-provider",
            "--report",
            str(report_path),
            "--image",
            str(gate.CANONICAL_IMAGE_PATH),
            "--provider",
            "openai",
            "--model",
            "qwen3.7-plus",
            "--scanner-mode",
            "fake-clean",
        ]
    )
    published = json.loads(report_path.read_text())

    assert exit_code == 1
    assert published["status"] == "FAIL"
    if mutation in {
        "fact_text",
        "cross_session_observation",
        "paired_cross_session_events",
    }:
        assert len(forged_attack_inputs) == 1
        forged_handle, wrapped_events, trusted_handle, target_session_id = forged_attack_inputs[0]
        assert forged_handle.session_id == target_session_id
        assert forged_handle.conversation_id == trusted_handle.conversation_id
        assert forged_handle.conversation is not trusted_handle.conversation
        assert isinstance(wrapped_events, WrappedNativeEvents)


@pytest.mark.parametrize(
    "mutation",
    [
        "lineage_source_mismatch",
        "duplicate_consumed_fact",
        "wrong_score_event",
        "wrong_review_event",
        "invalid_safe_event_count",
    ],
)
def test_product_path_tampered_audit_or_safe_summary_cannot_publish_pass(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_run_product_chain = gate._run_product_chain
    original_safe_summary = gate._safe_eventlog_summary
    original_create_app = gate.create_app
    applications: list[Any] = []

    def create_tampered_app(*args: Any, **kwargs: Any) -> Any:
        application = original_create_app(*args, **kwargs)
        applications.append(application)
        return application

    def llm_factory(session_id: str) -> ProductPathTestLLM:
        del session_id
        return ProductPathTestLLM(model="test-model")

    def run_with_product_test_llm(
        image_path: Path,
        provider: str,
        model: str,
        scanner_mode: str,
    ) -> dict[str, Any]:
        return original_run_product_chain(
            image_path,
            provider,
            model,
            scanner_mode,
            llm_factory=llm_factory,
            allow_test_llm=True,
        )

    def tampered_summary(*args: Any, **kwargs: Any) -> dict[str, object]:
        summary = original_safe_summary(*args, **kwargs)
        if mutation == "invalid_safe_event_count":
            counts = summary["eventCounts"]
            assert isinstance(counts, dict)
            observation_count = counts["observationCount"]
            assert isinstance(observation_count, int)
            counts["observationCount"] = observation_count + 1
        else:
            manager = applications[-1].state.conversation_manager
            original_project_lineage = manager.project_safe_completed_review_lineage

            def project_tampered_lineage(session_id: str) -> dict[str, object]:
                lineage = original_project_lineage(session_id)
                if mutation == "lineage_source_mismatch":
                    lineage["source"] = "obs_tampered"
                elif mutation == "duplicate_consumed_fact":
                    consumed = lineage["consumedFactIds"]
                    assert isinstance(consumed, list) and consumed
                    consumed.append(consumed[0])
                    lineage["consumedFactCount"] = len(consumed)
                elif mutation == "wrong_score_event":
                    lineage["score"] = "evt_score_tampered"
                else:
                    lineage["review"] = "evt_review_tampered"
                return lineage

            monkeypatch.setattr(
                manager,
                "project_safe_completed_review_lineage",
                project_tampered_lineage,
            )
        return summary

    monkeypatch.setattr(gate, "create_app", create_tampered_app)
    monkeypatch.setattr(gate, "_resolve_image", lambda path: gate.CANONICAL_IMAGE_PATH)
    monkeypatch.setattr(
        gate,
        "_provider_environment",
        lambda *args: (
            {
                "FOCUSPROOF_PROFILE": "local-dev",
                "FOCUSPROOF_MEDIA_ENABLED": "true",
                "FOCUSPROOF_MEDIA_SCANNER_MODE": "fake-clean",
                "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "false",
                "FOCUSPROOF_LLM_API_KEY": "safe-test-key",
            },
            "openai/qwen3.7-plus",
        ),
    )
    monkeypatch.setattr(gate, "_run_product_chain", run_with_product_test_llm)
    monkeypatch.setattr(gate, "_safe_eventlog_summary", tampered_summary)
    report_path = tmp_path / f"tampered-{mutation}.json"

    exit_code = gate.main(
        [
            "--execute-real-provider",
            "--report",
            str(report_path),
            "--image",
            str(gate.CANONICAL_IMAGE_PATH),
            "--provider",
            "openai",
            "--model",
            "qwen3.7-plus",
            "--scanner-mode",
            "fake-clean",
        ]
    )
    published = json.loads(report_path.read_text())

    assert exit_code != 0
    assert published["status"] != "PASS"
