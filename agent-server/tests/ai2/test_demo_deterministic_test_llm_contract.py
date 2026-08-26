from __future__ import annotations

import asyncio
import inspect
import json

from openhands.sdk.event import ObservationEvent
from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.sdk.testing.test_llm import TestLLM

import focusproof.openhands_runtime.demo_deterministic_provider as provider
from focusproof.openhands_runtime.demo_deterministic_provider import (
    extract_latest_image_evidence_id,
    is_strict_visual_prompt,
)
from focusproof.openhands_runtime.runtime_evidence_message_factory import (
    serialize_message_envelope,
)
from focusproof.openhands_runtime.tools.media_evidence import (
    _CORRECTIVE_VISUAL_FACT_PROMPT,
    _PRIMARY_VISUAL_FACT_PROMPT,
)
from focusproof.openhands_runtime.tools.verification import VerificationObservation


def test_openhands_testllm_completion_returns_llmresponse_shape() -> None:
    assistant_message = Message(
        role="assistant",
        content=[TextContent(text="ok")],
    )
    llm = TestLLM.from_messages([assistant_message], usage_id="contract-test")

    response = llm.completion(
        [Message(role="user", content=[TextContent(text="hello")])]
    )

    assert response.__class__.__module__ == "openhands.sdk.llm.llm_response"
    assert response.__class__.__name__ == "LLMResponse"
    assert response.message.model_dump(mode="json") == {
        "role": "assistant",
        "content": [{"cache_prompt": False, "type": "text", "text": "ok"}],
        "tool_calls": None,
        "tool_call_id": None,
        "name": None,
        "reasoning_content": None,
        "thinking_blocks": [],
        "responses_reasoning_item": None,
    }
    assert response.metrics.model_dump(mode="json")["accumulated_cost"] == 0.0
    assert response.raw_response.model == "test-model"


def test_openhands_testllm_acompletion_matches_completion_shape() -> None:
    assistant_message = Message(
        role="assistant",
        content=[TextContent(text="async ok")],
    )
    llm = TestLLM.from_messages([assistant_message], usage_id="contract-test")

    response = asyncio.run(
        llm.acompletion([Message(role="user", content=[TextContent(text="hello")])])
    )

    assert response.__class__.__module__ == "openhands.sdk.llm.llm_response"
    assert response.message.content[0].text == "async ok"
    assert response.metrics.model_name == "test-model"


def test_openhands_message_and_imagecontent_public_shape_uses_image_urls() -> None:
    message = Message(
        role="user",
        content=[
            TextContent(text="inspect this"),
            ImageContent(image_urls=["data:image/png;base64,QUJD"]),
        ],
    )

    payload = message.model_dump(mode="json")

    assert payload["role"] == "user"
    assert payload["content"][0] == {
        "cache_prompt": False,
        "type": "text",
        "text": "inspect this",
    }
    assert payload["content"][1] == {
        "cache_prompt": False,
        "type": "image",
        "image_urls": ["data:image/png;base64,QUJD"],
    }


def test_contract_red_extracts_image_evidence_id_from_envelope_message() -> None:
    envelope = serialize_message_envelope(
        schema_version=1,
        message_key="evidence:ev-image-1",
        kind="evidence",
        session_id="session-1",
        payload={
            "evidenceId": "ev-image-1",
            "evidenceType": "image/png",
            "contentHash": "sha256:test",
            "artifact_ref": "artifact://focusproof/ev-image-1",
        },
    )
    messages = [
        Message(
            role="user",
            content=[TextContent(text="non-envelope chatter")],
        ),
        Message(
            role="user",
            content=[TextContent(text=envelope)],
        ),
    ]

    assert extract_latest_image_evidence_id(messages) == "ev-image-1"


def test_contract_red_recognizes_primary_strict_visual_prompt() -> None:
    message = Message(
        role="user",
        content=[
            TextContent(text=_PRIMARY_VISUAL_FACT_PROMPT),
            ImageContent(image_urls=["data:image/png;base64,QUJD"]),
        ],
    )

    assert is_strict_visual_prompt(message) is True


def test_contract_red_recognizes_corrective_strict_visual_prompt() -> None:
    message = Message(
        role="user",
        content=[
            TextContent(text=_CORRECTIVE_VISUAL_FACT_PROMPT),
            ImageContent(image_urls=["data:image/png;base64,QUJD"]),
        ],
    )

    assert is_strict_visual_prompt(message) is True


def _demo_llm() -> TestLLM:
    demo_cls = getattr(provider, "DemoDeterministicTestLLM")
    return demo_cls(model="test-model")


def test_demo_llm_completion_signature_matches_official_testllm_contract() -> None:
    demo_cls = getattr(provider, "DemoDeterministicTestLLM")
    demo_signature = inspect.signature(demo_cls.completion)
    official_signature = inspect.signature(TestLLM.completion)

    assert list(demo_signature.parameters) == list(official_signature.parameters)
    for name in official_signature.parameters:
        assert str(demo_signature.parameters[name].annotation) == str(
            official_signature.parameters[name].annotation
        ) or (
            name == "tools"
            and str(demo_signature.parameters[name].annotation)
            == "Sequence[ToolDefinition[Any, Any]] | None"
            and str(official_signature.parameters[name].annotation)
            == "Sequence[ToolDefinition] | None"
        )
        assert demo_signature.parameters[name].kind == official_signature.parameters[name].kind
        assert demo_signature.parameters[name].default == official_signature.parameters[name].default
    assert str(demo_signature.return_annotation) == str(official_signature.return_annotation)


def _evidence_message(evidence_id: str, evidence_type: str) -> Message:
    envelope = serialize_message_envelope(
        schema_version=1,
        message_key=f"evidence:{evidence_id}",
        kind="evidence",
        session_id="session-1",
        payload={
            "evidenceId": evidence_id,
            "evidenceType": evidence_type,
            "contentHash": f"sha256:{evidence_id}",
        },
    )
    return Message(role="user", content=[TextContent(text=envelope)])


def _answer_message() -> Message:
    envelope = serialize_message_envelope(
        schema_version=1,
        message_key="answer:session-1:q1:1",
        kind="answer",
        session_id="session-1",
        payload={
            "questionId": "q1",
            "answer": "Native event continuity preserves deterministic replay after restart.",
            "version": 1,
        },
    )
    return Message(role="user", content=[TextContent(text=envelope)])


def _successful_media_observation_message(evidence_id: str) -> Message:
    observation = VerificationObservation.from_text(
        json.dumps(
            {
                "evidence_id": evidence_id,
                "capability": "image",
                "status": "success",
                "media_status": "ready",
                "facts": {
                    "media_status": "ready",
                    "visual_facts": [
                        "A browser capability is selected.",
                        "A success state is visible.",
                        "The evidence panel shows completion.",
                    ],
                },
                "source_refs": [evidence_id],
                "verifier_version": "2",
            },
            sort_keys=True,
        ),
        evidence_id=evidence_id,
        capability="image",
        status="success",
        facts={
            "media_status": "ready",
            "visual_facts": [
                "A browser capability is selected.",
                "A success state is visible.",
                "The evidence panel shows completion.",
            ],
        },
        weak_signals=[],
        source_refs=[evidence_id],
        verifier_version="2",
        started_at="2026-08-26T00:00:00+00:00",
        completed_at="2026-08-26T00:00:00+00:00",
    )
    return ObservationEvent(
        tool_name="focusproof_media_evidence_verification",
        tool_call_id="call_media_demo",
        observation=observation,
        action_id="action_media_demo",
    ).to_llm_message()


def test_contract_red_demo_llm_text_only_first_review_asks_for_learner_input() -> None:
    llm = _demo_llm()

    response = llm.completion([_evidence_message("ev-text-1", "text")], tools=[])

    assert response.message.tool_calls is not None
    assert response.message.tool_calls[0].name == "focusproof_learner_input"


def test_contract_red_demo_llm_image_review_requests_media_verification_first() -> None:
    llm = _demo_llm()

    response = llm.completion([_evidence_message("ev-image-1", "image/png")], tools=[])

    assert response.message.tool_calls is not None
    assert response.message.tool_calls[0].name == "focusproof_media_evidence_verification"
    assert json.loads(response.message.tool_calls[0].arguments) == {"evidence_id": "ev-image-1"}


def test_contract_red_demo_llm_nested_visual_prompt_returns_strict_json() -> None:
    llm = _demo_llm()

    response = llm.completion(
        [
            Message(
                role="user",
                content=[
                    TextContent(text=_PRIMARY_VISUAL_FACT_PROMPT),
                    ImageContent(image_urls=["data:image/png;base64,QUJD"]),
                ],
            )
        ],
        tools=None,
    )

    payload = json.loads(response.message.content[0].text)
    assert len(payload["visual_facts"]) >= 3


def test_contract_red_demo_llm_corrective_visual_prompt_returns_strict_json() -> None:
    llm = _demo_llm()

    response = llm.completion(
        [
            Message(
                role="user",
                content=[
                    TextContent(text=_CORRECTIVE_VISUAL_FACT_PROMPT),
                    ImageContent(image_urls=["data:image/png;base64,QUJD"]),
                ],
            )
        ],
        tools=None,
    )

    payload = json.loads(response.message.content[0].text)
    assert len(payload["visual_facts"]) >= 3


def test_contract_red_demo_llm_does_not_repeat_media_tool_after_success_observation() -> None:
    llm = _demo_llm()

    response = llm.completion(
        [
            _evidence_message("ev-image-1", "image/png"),
            _successful_media_observation_message("ev-image-1"),
        ],
        tools=[],
    )

    assert response.message.tool_calls is not None
    assert response.message.tool_calls[0].name == "focusproof_learner_input"


def test_contract_red_demo_llm_with_answer_returns_review_draft() -> None:
    llm = _demo_llm()

    response = llm.completion(
        [_evidence_message("ev-text-1", "text"), _answer_message()],
        tools=[],
    )

    assert response.message.tool_calls is not None
    assert response.message.tool_calls[0].name == "focusproof_review_draft"
