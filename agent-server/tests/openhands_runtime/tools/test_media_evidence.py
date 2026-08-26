from __future__ import annotations

import json

import pytest
from types import SimpleNamespace
from openhands.sdk.llm import ImageContent, Message, TextContent

from focusproof.openhands_runtime.runtime_contributions import (
    MediaEvidenceAccessDenied,
    MediaEvidenceFacts,
    MediaEvidenceNotReady,
)
from focusproof.openhands_runtime.tools.verification import EvidenceReferenceAction


class RecordingMediaRepository:
    def __init__(
        self,
        result: MediaEvidenceFacts | BaseException,
    ) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def get_media_evidence_facts(
        self,
        session_id: str,
        evidence_id: str,
    ) -> MediaEvidenceFacts:
        self.calls.append((session_id, evidence_id))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _facts() -> MediaEvidenceFacts:
    return MediaEvidenceFacts(
        evidence_id="ev_img",
        receipt_id="receipt_img",
        attempt_id="attempt_img",
        scan_result="clean",
        artifact_ref="artifact://safe-image-ref",
        artifact_sha256="ab" * 32,
        media_type="image/png",
        normalized_sha256="ab" * 32,
        byte_size=12345,
        width=640,
        height=480,
        learner_explanation=(
            "I compared the nonce, gas limit, and confirmation step in a payment transaction."
        ),
    )


def test_media_executor_reads_authoritative_scoped_facts_by_evidence_id() -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    repository = RecordingMediaRepository(_facts())
    result = MediaEvidenceVerificationExecutor(repository, "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_img")
    )

    assert repository.calls == [("sess_1", "ev_img")]
    assert result.status == "inconclusive"
    assert result.facts["media_status"] == "not_ready"
    assert result.error_code == "media_evidence_not_ready"
    assert result.facts == {"media_status": "not_ready"}


def test_media_observation_dto_does_not_leak_paths_keys_owner_session_or_secrets() -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    result = MediaEvidenceVerificationExecutor(
        RecordingMediaRepository(_facts()),
        "sess_secret",
    )(EvidenceReferenceAction(evidence_id="ev_img"))
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)

    assert "/home/" not in serialized
    assert "\\\\" not in serialized
    assert "object_key" not in serialized
    assert "opaque_key" not in serialized
    assert "owner_id" not in serialized
    assert "sess_secret" not in serialized
    assert "api_key" not in serialized.lower()
    assert "token" not in serialized.lower()
    assert any(isinstance(item, TextContent) for item in result.content)


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (KeyError("ev_missing"), "failed", "evidence_not_found"),
        (
            MediaEvidenceAccessDenied("owner mismatch: owner-secret"),
            "failed",
            "media_evidence_access_denied",
        ),
        (
            MediaEvidenceNotReady("artifact pending private/path"),
            "inconclusive",
            "media_evidence_not_ready",
        ),
    ],
)
def test_media_executor_returns_structured_safe_failures(
    error: BaseException,
    status: str,
    code: str,
) -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    result = MediaEvidenceVerificationExecutor(
        RecordingMediaRepository(error),
        "sess_1",
    )(EvidenceReferenceAction(evidence_id="ev_img"))
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)

    assert result.status == status
    assert result.error_code == code
    assert result.safe_error_message
    assert "owner-secret" not in serialized
    assert "private/path" not in serialized
    assert "ev_missing" not in serialized


def test_media_tool_accepts_only_evidence_reference_and_is_read_only() -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        FocusProofMediaEvidenceVerificationTool,
    )

    assert set(EvidenceReferenceAction.model_fields) == {"evidence_id"}
    annotations = FocusProofMediaEvidenceVerificationTool.annotations_for_focusproof()
    assert annotations.readOnlyHint is True
    assert annotations.destructiveHint is False
    assert annotations.idempotentHint is True
    assert annotations.openWorldHint is False


def test_media_tool_create_binds_repository_and_session_without_exposing_raw_fields() -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        FocusProofMediaEvidenceVerificationTool,
    )

    repository = RecordingMediaRepository(_facts())
    (tool,) = FocusProofMediaEvidenceVerificationTool.create(
        session_id="sess_1",
        repository=repository,
    )
    assert tool.executor is not None
    result = tool.executor(EvidenceReferenceAction(evidence_id="ev_img"))

    assert repository.calls == [("sess_1", "ev_img")]
    assert result.status == "inconclusive"
    assert result.facts["media_status"] == "not_ready"
    assert "path" not in json.dumps(result.facts, sort_keys=True).lower()


class VisualRepository(RecordingMediaRepository):
    def get_media_evidence_content(
        self,
        session_id: str,
        evidence_id: str,
    ) -> object:
        facts = self.get_media_evidence_facts(session_id, evidence_id)
        return SimpleNamespace(facts=facts, payload=b"bounded-image-payload")


def test_media_executor_missing_llm_has_complete_not_run_diagnostics() -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    result = MediaEvidenceVerificationExecutor(VisualRepository(_facts()), "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_img")
    )

    assert result.error_code == "media_visual_provider_not_ready"
    assert result.facts["visual_diagnostics"] == {
        "visualProviderAttempted": False,
        "visualProviderResponseReceived": False,
        "visualProviderCompletionSucceeded": False,
        "visualProviderCompletionCalls": 0,
        "visualResponseFormat": "not_run",
        "visualResponseParseStage": "not_started",
        "visualProviderErrorCategory": "not_run",
        "responseTextLength": None,
    }


class RecordingVisualLlm:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.messages: list[list[Message]] = []

    def completion(self, messages: list[Message]) -> object:
        self.messages.append(messages)
        return SimpleNamespace(
            message=Message(
                role="assistant",
                content=[TextContent(text=self.response_text)],
            )
        )


class SequentialVisualLlm:
    def __init__(self, response_texts: list[str]) -> None:
        self.response_texts = list(response_texts)
        self.messages: list[list[Message]] = []

    def completion(self, messages: list[Message]) -> object:
        self.messages.append(messages)
        response_text = self.response_texts.pop(0)
        return SimpleNamespace(
            message=Message(
                role="assistant",
                content=[TextContent(text=response_text)],
            )
        )


def test_media_executor_temporarily_calls_visual_llm_and_persists_only_facts() -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    repository = VisualRepository(_facts())
    llm = RecordingVisualLlm(
        json.dumps(
            {
                "visual_facts": [
                    "The image has a white background.",
                    "A black rectangular border surrounds the content.",
                    "The center contains large dark text.",
                ]
            }
        )
    )
    conversation = SimpleNamespace(agent=SimpleNamespace(llm=llm))

    result = MediaEvidenceVerificationExecutor(repository, "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_img"),
        conversation=conversation,
    )

    assert result.status == "success"
    assert result.facts["media_status"] == "ready"
    assert result.facts["visual_facts"] == [
        "The image has a white background.",
        "A black rectangular border surrounds the content.",
        "The center contains large dark text.",
    ]
    prompt = llm.messages[0]
    image_parts = [
        item for message in prompt for item in message.content if isinstance(item, ImageContent)
    ]
    assert len(image_parts) == 1
    assert image_parts[0].image_urls[0].startswith("data:image/png;base64,")
    serialized = result.model_dump_json()
    assert "data:image" not in serialized
    assert "base64" not in serialized.lower()
    assert "bounded-image-payload" not in serialized


@pytest.mark.parametrize(
    ("response_text", "media_status"),
    [
        ('{"visual_facts": []}', "not_ready"),
        ('{"visual_facts": ["one", "two"]}', "not_ready"),
        ("not-json", "not_ready"),
    ],
)
def test_media_executor_requires_three_structured_visual_facts(
    response_text: str,
    media_status: str,
) -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    result = MediaEvidenceVerificationExecutor(
        VisualRepository(_facts()),
        "sess_1",
    )(
        EvidenceReferenceAction(evidence_id="ev_img"),
        conversation=SimpleNamespace(agent=SimpleNamespace(llm=RecordingVisualLlm(response_text))),
    )

    assert result.status == "inconclusive"
    assert result.facts["media_status"] == media_status
    assert result.facts.get("visual_facts", []) == []


def test_media_tool_uses_existing_verification_observation_schema() -> None:
    from openhands.sdk.event.base import Event
    from openhands.sdk.tool import Observation
    from focusproof.openhands_runtime.tools import media_evidence as module
    from focusproof.openhands_runtime.tools.verification import VerificationObservation

    Event.model_json_schema()

    (tool,) = module.FocusProofMediaEvidenceVerificationTool.create(
        session_id="sess_schema",
        repository=RecordingMediaRepository(_facts()),
    )
    assert tool.observation_type is VerificationObservation
    assert not any(cls.__module__ == module.__name__ for cls in Observation.__subclasses__())


@pytest.mark.parametrize(
    ("response_text", "expected_format"),
    [
        ('{"visual_facts":["one","two","three"]}', "plain_json"),
        (
            '  ```json\n{"visual_facts":["one","two","three"]}\n```  ',
            "fenced_json",
        ),
        (
            '\n```\n{"visual_facts":["one","two","three"]}\n```\n',
            "fenced_json",
        ),
    ],
)
def test_visual_response_parser_accepts_only_plain_or_single_fenced_json(
    response_text: str,
    expected_format: str,
) -> None:
    from focusproof.openhands_runtime.tools.media_evidence import _parse_visual_response

    facts, response_format = _parse_visual_response(response_text)

    assert facts == ["one", "two", "three"]
    assert response_format == expected_format


@pytest.mark.parametrize(
    "response_text",
    [
        'Here is the result:\n```json\n{"visual_facts":["one","two","three"]}\n```',
        '```json\n{"visual_facts":["one","two","three"]}\n```\nThanks.',
        '```json\n{"visual_facts":["one","two","three"]}\n```\n```json\n{}\n```',
        '["one", "two", "three"]',
        "{}",
        '{"visual_facts":"one"}',
        '{"visual_facts":["one", 2, "three"]}',
        'prefix {"visual_facts":["one","two","three"]} suffix',
    ],
)
def test_visual_response_parser_strictly_rejects_ambiguous_or_invalid_payloads(
    response_text: str,
) -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        VisualResponseParseError,
        _parse_visual_response,
    )

    with pytest.raises(VisualResponseParseError):
        _parse_visual_response(response_text)


@pytest.mark.parametrize(
    ("response_text", "expected"),
    [
        ('{"visual_facts":["one", "   ", "three"]}', ["one", "three"]),
        ('{"visual_facts":[" one ", "ONE", "three"]}', ["one", "three"]),
    ],
)
def test_visual_response_parser_normalizes_underfull_structured_payloads_for_corrective_retry(
    response_text: str,
    expected: list[str],
) -> None:
    from focusproof.openhands_runtime.tools.media_evidence import _parse_visual_response

    facts, response_format = _parse_visual_response(response_text)

    assert facts == expected
    assert response_format == "plain_json"


@pytest.mark.parametrize(
    ("response_text", "error_code", "response_format", "parse_stage", "category"),
    [
        ("", "media_visual_response_empty", "empty", "response_text", "empty_response"),
        (
            "not-json",
            "media_visual_response_non_json",
            "non_json",
            "json_decode",
            "response_non_json",
        ),
    ],
)
def test_media_executor_records_safe_visual_parse_diagnostics(
    response_text: str,
    error_code: str,
    response_format: str,
    parse_stage: str,
    category: str,
) -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    result = MediaEvidenceVerificationExecutor(VisualRepository(_facts()), "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_img"),
        conversation=SimpleNamespace(agent=SimpleNamespace(llm=RecordingVisualLlm(response_text))),
    )

    assert result.error_code == error_code
    assert result.facts["visual_diagnostics"] == {
        "visualProviderAttempted": True,
        "visualProviderResponseReceived": True,
        "visualProviderCompletionSucceeded": bool(response_text.strip()),
        "visualProviderCompletionCalls": 1,
        "visualResponseFormat": response_format,
        "visualResponseParseStage": parse_stage,
        "visualProviderErrorCategory": category,
        "responseTextLength": len(response_text),
    }
    serialized = result.model_dump_json()
    assert response_text not in serialized or not response_text
    assert "base64" not in serialized.lower()


def test_media_executor_retries_once_when_first_json_has_too_few_visual_facts() -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    llm = SequentialVisualLlm(
        [
            '{"visual_facts":["one","two"]}',
            '{"visual_facts":["one","two","three"]}',
        ]
    )
    result = MediaEvidenceVerificationExecutor(VisualRepository(_facts()), "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_img"),
        conversation=SimpleNamespace(agent=SimpleNamespace(llm=llm)),
    )

    assert result.status == "success"
    assert result.facts["visual_facts"] == ["one", "two", "three"]
    assert result.facts["visual_diagnostics"] == {
        "visualProviderAttempted": True,
        "visualProviderResponseReceived": True,
        "visualProviderCompletionSucceeded": True,
        "visualProviderCompletionCalls": 2,
        "visualResponseFormat": "plain_json",
        "visualResponseParseStage": "complete",
        "visualProviderErrorCategory": "none",
        "responseTextLength": len('{"visual_facts":["one","two","three"]}'),
    }
    assert len(llm.messages) == 2
    retry_prompt = llm.messages[1][0].content[0]
    assert isinstance(retry_prompt, TextContent)
    assert "fewer than 3 distinct visual facts" in retry_prompt.text
    assert "3 to 5" in retry_prompt.text


def test_media_executor_fails_closed_after_second_underfull_json_response() -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    llm = SequentialVisualLlm(
        [
            '{"visual_facts":["one","two"]}',
            '{"visual_facts":["one","two"]}',
        ]
    )
    result = MediaEvidenceVerificationExecutor(VisualRepository(_facts()), "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_img"),
        conversation=SimpleNamespace(agent=SimpleNamespace(llm=llm)),
    )

    assert result.status == "inconclusive"
    assert result.error_code == "media_visual_response_invalid_schema"
    assert result.facts["visual_diagnostics"] == {
        "visualProviderAttempted": True,
        "visualProviderResponseReceived": True,
        "visualProviderCompletionSucceeded": True,
        "visualProviderCompletionCalls": 2,
        "visualResponseFormat": "plain_json",
        "visualResponseParseStage": "schema_validation",
        "visualProviderErrorCategory": "response_invalid_schema",
        "responseTextLength": len('{"visual_facts":["one","two"]}'),
    }


def test_media_executor_records_transport_failure_without_exception_text() -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    class RaisingVisualLlm:
        def completion(self, _messages: list[Message]) -> object:
            raise RuntimeError("provider-secret raw response")

    result = MediaEvidenceVerificationExecutor(VisualRepository(_facts()), "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_img"),
        conversation=SimpleNamespace(agent=SimpleNamespace(llm=RaisingVisualLlm())),
    )

    assert result.error_code == "media_visual_provider_error"
    assert result.facts["visual_diagnostics"] == {
        "visualProviderAttempted": True,
        "visualProviderResponseReceived": False,
        "visualProviderCompletionSucceeded": False,
        "visualProviderCompletionCalls": 1,
        "visualResponseFormat": "not_run",
        "visualResponseParseStage": "provider_completion",
        "visualProviderErrorCategory": "provider_transport_error",
        "responseTextLength": None,
    }
    serialized = result.model_dump_json()
    assert "provider-secret" not in serialized
    assert "raw response" not in serialized


def test_media_executor_records_successful_fenced_visual_completion() -> None:
    from focusproof.openhands_runtime.tools.media_evidence import (
        MediaEvidenceVerificationExecutor,
    )

    response_text = '```json\n{"visual_facts":["one","two","three"]}\n```'
    result = MediaEvidenceVerificationExecutor(VisualRepository(_facts()), "sess_1")(
        EvidenceReferenceAction(evidence_id="ev_img"),
        conversation=SimpleNamespace(agent=SimpleNamespace(llm=RecordingVisualLlm(response_text))),
    )

    assert result.status == "success"
    assert result.facts["visual_diagnostics"] == {
        "visualProviderAttempted": True,
        "visualProviderResponseReceived": True,
        "visualProviderCompletionSucceeded": True,
        "visualProviderCompletionCalls": 1,
        "visualResponseFormat": "fenced_json",
        "visualResponseParseStage": "complete",
        "visualProviderErrorCategory": "none",
        "responseTextLength": len(response_text),
    }
