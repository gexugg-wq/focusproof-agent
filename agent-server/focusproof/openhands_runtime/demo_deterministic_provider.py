from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
import json

from openhands.sdk.llm import ImageContent, Message, MessageToolCall, TextContent
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.streaming import TokenCallbackType
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import ToolDefinition

from focusproof.openhands_runtime.evidence_messages import FocusProofMessageEnvelope
from focusproof.openhands_runtime.tools.media_evidence import (
    _CORRECTIVE_VISUAL_FACT_PROMPT,
    _PRIMARY_VISUAL_FACT_PROMPT,
)

if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLMCallContext

_STRICT_VISUAL_PROMPTS = {
    _PRIMARY_VISUAL_FACT_PROMPT,
    _CORRECTIVE_VISUAL_FACT_PROMPT,
}
_VISUAL_FACTS = [
    "A browser capability is selected.",
    "A success state is visible.",
    "The evidence panel shows completion.",
]
_LEARNER_INPUT_ARGUMENTS = {
    "question": "Explain why native event continuity matters after restart.",
    "reason": "Confirm learner understanding after durable recovery.",
    "requested_evidence_type": "text",
}
_REVIEW_DRAFT_ARGUMENTS = {
    "credibility_findings": ["Evidence is repository-backed."],
    "understanding_findings": [
        "The learner explains that durable IDs survive restart."
    ],
    "contradictions": [],
    "recommended_next_step": "Add one concrete replay example.",
    "confidence": 0.8,
}


def extract_latest_image_evidence_id(messages: Sequence[Message]) -> str | None:
    for message in reversed(messages):
        for item in message.content:
            if not isinstance(item, TextContent):
                continue
            try:
                envelope = FocusProofMessageEnvelope.model_validate_json(item.text)
            except ValueError:
                continue
            evidence_type = envelope.payload.get("evidenceType")
            evidence_id = envelope.payload.get("evidenceId")
            if (
                envelope.kind == "evidence"
                and isinstance(evidence_type, str)
                and evidence_type.startswith("image/")
                and isinstance(evidence_id, str)
                and evidence_id
            ):
                return evidence_id
    return None


def is_strict_visual_prompt(message: Message) -> bool:
    has_image = any(isinstance(item, ImageContent) for item in message.content)
    if not has_image:
        return False
    return any(
        isinstance(item, TextContent) and item.text.strip() in _STRICT_VISUAL_PROMPTS
        for item in message.content
    )


def _has_answer_envelope(messages: Sequence[Message]) -> bool:
    for message in messages:
        for item in message.content:
            if not isinstance(item, TextContent):
                continue
            try:
                envelope = FocusProofMessageEnvelope.model_validate_json(item.text)
            except ValueError:
                continue
            if envelope.kind == "answer":
                return True
    return False


def _tool_message_text(message: Message) -> str:
    return "\n".join(
        item.text for item in message.content if isinstance(item, TextContent)
    )


def _has_successful_media_observation(
    messages: Sequence[Message],
    evidence_id: str | None,
) -> bool:
    for message in messages:
        if (
            message.role != "tool"
            or message.name != "focusproof_media_evidence_verification"
        ):
            continue
        raw = _tool_message_text(message)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        facts = payload.get("facts")
        visual_facts = facts.get("visual_facts") if isinstance(facts, dict) else None
        if (
            payload.get("status") == "success"
            and isinstance(payload.get("evidence_id"), str)
            and (evidence_id is None or payload.get("evidence_id") == evidence_id)
            and isinstance(visual_facts, list)
            and len(visual_facts) >= 3
        ):
            return True
    return False


class DemoDeterministicTestLLM(TestLLM):
    def vision_is_active(self) -> bool:
        return True

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition[Any, Any]] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        if self._scripted_responses:
            return super().completion(
                messages=messages,
                tools=tools,
                add_security_risk_prediction=add_security_risk_prediction,
                on_token=on_token,
                call_context=call_context,
                **kwargs,
            )

        if tools is None and any(is_strict_visual_prompt(message) for message in messages):
            self._scripted_responses.append(
                Message(
                    role="assistant",
                    content=[
                        TextContent(
                            text=json.dumps({"visual_facts": _VISUAL_FACTS})
                        )
                    ],
                )
            )
            return super().completion(
                messages=messages,
                tools=tools,
                add_security_risk_prediction=add_security_risk_prediction,
                on_token=on_token,
                call_context=call_context,
                **kwargs,
            )

        evidence_id = extract_latest_image_evidence_id(messages)
        has_answer = _has_answer_envelope(messages)
        has_media_success = _has_successful_media_observation(messages, evidence_id)

        if evidence_id is not None and not has_media_success:
            self._scripted_responses.append(
                Message(
                    role="assistant",
                    content=[TextContent(text="Verify the uploaded image evidence.")],
                    tool_calls=[
                        MessageToolCall(
                            id="call_demo_media_verification",
                            name="focusproof_media_evidence_verification",
                            arguments=json.dumps({"evidence_id": evidence_id}),
                            origin="completion",
                        )
                    ],
                )
            )
        elif has_answer:
            self._scripted_responses.append(
                Message(
                    role="assistant",
                    content=[TextContent(text="Submit the staging review draft")],
                    tool_calls=[
                        MessageToolCall(
                            id="call_demo_review_draft",
                            name="focusproof_review_draft",
                            arguments=json.dumps(_REVIEW_DRAFT_ARGUMENTS),
                            origin="completion",
                        )
                    ],
                )
            )
        else:
            self._scripted_responses.append(
                Message(
                    role="assistant",
                    content=[TextContent(text="Ask for learner confirmation")],
                    tool_calls=[
                        MessageToolCall(
                            id="call_demo_learner_input",
                            name="focusproof_learner_input",
                            arguments=json.dumps(_LEARNER_INPUT_ARGUMENTS),
                            origin="completion",
                        )
                    ],
                )
            )
        return super().completion(
            messages=messages,
            tools=tools,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )


def build_demo_deterministic_test_llm(session_id: str) -> DemoDeterministicTestLLM:
    return DemoDeterministicTestLLM(
        model="test-model",
        usage_id=f"focusproof-demo-deterministic-{session_id}",
    )
