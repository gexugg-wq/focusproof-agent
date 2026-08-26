from __future__ import annotations

import base64
import json
import re
from collections.abc import Sequence
from typing import Any, ClassVar, Literal, Self, cast

from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.sdk.tool import ToolAnnotations, ToolDefinition, ToolExecutor

from focusproof.media_adapters.media_message_content import MediaMessageContentError
from focusproof.openhands_runtime.media_evidence_facts import (
    MediaEvidenceAccessDenied,
    MediaEvidenceFacts,
    MediaEvidenceNotReady,
    ScopedMediaEvidenceRepository,
)
from focusproof.openhands_runtime.tools import read_only_annotations
from focusproof.openhands_runtime.tools.verification import (
    EvidenceReferenceAction,
    VerificationObservation,
    utc_now,
)

_VERIFIER_VERSION = "2"
_MIN_VISUAL_FACTS = 3
_MAX_VISUAL_FACTS = 8
_MAX_VISUAL_FACT_CHARS = 500
MediaVerificationStatus = Literal["ready", "not_ready", "rejected", "error"]
_PRIMARY_VISUAL_FACT_PROMPT = (
    "Inspect this learning-evidence image. Return strict JSON only: "
    '{"visual_facts":["fact 1","fact 2","fact 3"]}. '
    "Provide 3 to 8 independently observable, concrete pixel-grounded facts. "
    "Do not score learning, infer identity, or repeat the learner explanation."
)
_CORRECTIVE_VISUAL_FACT_PROMPT = (
    "Your previous response was valid JSON but contained fewer than 3 distinct "
    'visual facts. Return strict JSON only: {"visual_facts":["fact 1","fact 2","fact 3"]}. '
    "Return 3 to 5 mutually independent, content-specific, concrete pixel-grounded "
    "facts visible in this image. Do not score learning, infer identity, or repeat "
    "the learner explanation."
)


class VisualResponseParseError(RuntimeError):
    def __init__(self, response_format: str, parse_stage: str, category: str) -> None:
        super().__init__(category)
        self.response_format = response_format
        self.parse_stage = parse_stage
        self.category = category


class VisualResponseSchemaError(VisualResponseParseError, ValueError):
    pass


_SINGLE_JSON_FENCE = re.compile(
    r"\A\s*```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*\s*\Z",
    re.DOTALL,
)


def _not_run_visual_diagnostics() -> dict[str, object]:
    return {
        "visualProviderAttempted": False,
        "visualProviderResponseReceived": False,
        "visualProviderCompletionSucceeded": False,
        "visualProviderCompletionCalls": 0,
        "visualResponseFormat": "not_run",
        "visualResponseParseStage": "not_started",
        "visualProviderErrorCategory": "not_run",
        "responseTextLength": None,
    }


class MediaEvidenceVerificationExecutor(
    ToolExecutor[EvidenceReferenceAction, VerificationObservation]
):
    def __init__(
        self,
        repository: ScopedMediaEvidenceRepository | None,
        session_id: str,
    ) -> None:
        self._repository = repository
        self._session_id = session_id

    def __call__(
        self,
        action: EvidenceReferenceAction,
        conversation: Any | None = None,
    ) -> VerificationObservation:
        started_at = utc_now()
        repository = self._repository
        if repository is None:
            from focusproof.openhands_runtime.tool_registry import (
                get_repository_provider,
            )

            repository = cast(ScopedMediaEvidenceRepository, get_repository_provider())
        try:
            facts = repository.get_media_evidence_facts(
                self._session_id,
                action.evidence_id,
            )
            content_getter = getattr(repository, "get_media_evidence_content", None)
            if content_getter is None:
                raise MediaEvidenceNotReady("media content reader is unavailable")
            content = content_getter(self._session_id, action.evidence_id)
            _assert_same_artifact(facts, content)
        except KeyError:
            return _failure(
                action.evidence_id,
                started_at,
                status="failed",
                media_status="not_ready",
                error_code="evidence_not_found",
                safe_error_message="Evidence was not found.",
            )
        except MediaEvidenceAccessDenied:
            return _failure(
                action.evidence_id,
                started_at,
                status="failed",
                media_status="rejected",
                error_code="media_evidence_access_denied",
                safe_error_message="Media evidence is not available to this Session.",
            )
        except MediaEvidenceNotReady:
            return _failure(
                action.evidence_id,
                started_at,
                status="inconclusive",
                media_status="not_ready",
                error_code="media_evidence_not_ready",
                safe_error_message="Media evidence is not ready for verification.",
            )
        except (MediaMessageContentError, ValueError):
            return _failure(
                action.evidence_id,
                started_at,
                status="failed",
                media_status="rejected",
                error_code="media_evidence_rejected",
                safe_error_message="Media evidence failed integrity verification.",
            )
        except Exception:
            return _failure(
                action.evidence_id,
                started_at,
                status="inconclusive",
                media_status="error",
                error_code="media_evidence_unavailable",
                safe_error_message="Media evidence could not be inspected safely.",
            )

        llm = getattr(getattr(conversation, "agent", None), "llm", None)
        visual_diagnostics = _not_run_visual_diagnostics()
        if llm is None:
            return _failure(
                action.evidence_id,
                started_at,
                status="inconclusive",
                media_status="not_ready",
                error_code="media_visual_provider_not_ready",
                safe_error_message="Visual verification is not ready.",
                visual_diagnostics=visual_diagnostics,
            )
        try:
            visual_facts = _inspect_visual_content(
                llm,
                facts,
                bytes(getattr(content, "payload")),
                visual_diagnostics,
            )
        except VisualResponseParseError as exc:
            error_codes = {
                "empty_response": "media_visual_response_empty",
                "response_non_json": "media_visual_response_non_json",
                "response_invalid_schema": "media_visual_response_invalid_schema",
            }
            return _failure(
                action.evidence_id,
                started_at,
                status="inconclusive",
                media_status="not_ready",
                error_code=error_codes[exc.category],
                safe_error_message="Visual verification produced insufficient facts.",
                visual_diagnostics=visual_diagnostics,
            )
        except Exception:
            visual_diagnostics.update(
                {
                    "visualResponseParseStage": "provider_completion",
                    "visualProviderErrorCategory": "provider_transport_error",
                }
            )
            return _failure(
                action.evidence_id,
                started_at,
                status="inconclusive",
                media_status="error",
                error_code="media_visual_provider_error",
                safe_error_message="Visual verification could not be completed safely.",
                visual_diagnostics=visual_diagnostics,
            )
        return _success(facts, visual_facts, started_at, visual_diagnostics)


def _assert_same_artifact(facts: MediaEvidenceFacts, content: object) -> None:
    artifact = getattr(content, "facts")
    expected = (
        facts.evidence_id,
        facts.artifact_ref,
        facts.media_type,
        facts.normalized_sha256,
        facts.byte_size,
    )
    actual = (
        getattr(artifact, "evidence_id"),
        getattr(artifact, "artifact_ref"),
        getattr(artifact, "media_type"),
        getattr(artifact, "normalized_sha256"),
        getattr(artifact, "byte_size"),
    )
    if actual != expected:
        raise ValueError("media artifact identity changed during verification")


def _parse_visual_response(raw: str) -> tuple[list[str], str]:
    stripped = raw.strip()
    if not stripped:
        raise VisualResponseParseError("empty", "response_text", "empty_response")
    fenced = _SINGLE_JSON_FENCE.fullmatch(raw)
    if fenced is not None:
        response_format = "fenced_json"
        document = fenced.group("body")
    else:
        response_format = "plain_json"
        document = stripped
    try:
        decoded = json.loads(document)
    except json.JSONDecodeError as exc:
        raise VisualResponseParseError("non_json", "json_decode", "response_non_json") from exc
    if not isinstance(decoded, dict):
        raise VisualResponseSchemaError(
            "invalid_schema", "schema_validation", "response_invalid_schema"
        )
    candidates = decoded.get("visual_facts")
    if not isinstance(candidates, list) or not all(isinstance(item, str) for item in candidates):
        raise VisualResponseSchemaError(
            "invalid_schema", "schema_validation", "response_invalid_schema"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates[:_MAX_VISUAL_FACTS]:
        value = " ".join(item.split())[:_MAX_VISUAL_FACT_CHARS]
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized, response_format


def _visual_prompt(media_type: str, encoded: str, *, corrective: bool) -> Message:
    return Message(
        role="user",
        content=[
            TextContent(
                text=(
                    _CORRECTIVE_VISUAL_FACT_PROMPT
                    if corrective
                    else _PRIMARY_VISUAL_FACT_PROMPT
                )
            ),
            ImageContent(
                image_urls=[f"data:{media_type};base64,{encoded}"],
            ),
        ],
    )


def _request_visual_completion(
    llm: object,
    prompt: Message,
    diagnostics: dict[str, object],
) -> str:
    diagnostics["visualProviderAttempted"] = True
    calls = diagnostics.get("visualProviderCompletionCalls", 0)
    if not isinstance(calls, int):
        raise ValueError("visual provider diagnostics are invalid")
    diagnostics["visualProviderCompletionCalls"] = calls + 1
    try:
        response = getattr(llm, "completion")([prompt])
    except Exception:
        diagnostics["visualProviderCompletionSucceeded"] = False
        raise
    prior_received = diagnostics.get("visualProviderResponseReceived")
    diagnostics["visualProviderResponseReceived"] = bool(prior_received) or response is not None
    message = getattr(response, "message")
    raw = "".join(item.text for item in message.content if isinstance(item, TextContent))
    diagnostics["visualProviderCompletionSucceeded"] = bool(raw.strip())
    diagnostics["responseTextLength"] = len(raw)
    return raw


def _inspect_visual_content(
    llm: object,
    facts: MediaEvidenceFacts,
    payload: bytes,
    diagnostics: dict[str, object],
) -> list[str]:
    encoded = base64.b64encode(payload).decode("ascii")
    primary_prompt = _visual_prompt(facts.media_type, encoded, corrective=False)
    raw = _request_visual_completion(llm, primary_prompt, diagnostics)
    try:
        visual_facts, response_format = _parse_visual_response(raw)
    except VisualResponseParseError as exc:
        diagnostics.update(
            {
                "visualResponseFormat": exc.response_format,
                "visualResponseParseStage": exc.parse_stage,
                "visualProviderErrorCategory": exc.category,
            }
        )
        raise
    if len(visual_facts) < _MIN_VISUAL_FACTS:
        corrective_prompt = _visual_prompt(facts.media_type, encoded, corrective=True)
        corrective_raw = _request_visual_completion(llm, corrective_prompt, diagnostics)
        try:
            visual_facts, response_format = _parse_visual_response(corrective_raw)
        except VisualResponseParseError as exc:
            diagnostics.update(
                {
                    "visualResponseFormat": exc.response_format,
                    "visualResponseParseStage": exc.parse_stage,
                    "visualProviderErrorCategory": exc.category,
                }
            )
            raise
        if len(visual_facts) < _MIN_VISUAL_FACTS:
            diagnostics.update(
                {
                    "visualResponseFormat": response_format,
                    "visualResponseParseStage": "schema_validation",
                    "visualProviderErrorCategory": "response_invalid_schema",
                }
            )
            raise VisualResponseSchemaError(
                "invalid_schema",
                "schema_validation",
                "response_invalid_schema",
            )
    diagnostics.update(
        {
            "visualResponseFormat": response_format,
            "visualResponseParseStage": "complete",
            "visualProviderErrorCategory": "none",
        }
    )
    return visual_facts


def _safe_facts(
    facts: MediaEvidenceFacts,
    visual_facts: list[str],
) -> dict[str, object]:
    return {
        "artifact_ref": facts.artifact_ref,
        "media_type": facts.media_type,
        "normalized_sha256": facts.normalized_sha256,
        "byte_size": facts.byte_size,
        "width": facts.width,
        "height": facts.height,
        "learner_explanation": facts.learner_explanation,
        "visual_facts": visual_facts,
        "media_status": "ready",
    }


def _success(
    facts: MediaEvidenceFacts,
    visual_facts: list[str],
    started_at: object,
    visual_diagnostics: dict[str, object],
) -> VerificationObservation:
    safe_facts = _safe_facts(facts, visual_facts)
    safe_facts["visual_diagnostics"] = visual_diagnostics
    source_refs = [
        facts.evidence_id,
        facts.artifact_ref,
        f"sha256:{facts.normalized_sha256}",
    ]
    payload = {
        "evidence_id": facts.evidence_id,
        "capability": "image",
        "status": "success",
        "media_status": "ready",
        "facts": safe_facts,
        "source_refs": source_refs,
        "verifier_version": _VERIFIER_VERSION,
    }
    return VerificationObservation.from_text(
        json.dumps(payload, sort_keys=True),
        evidence_id=facts.evidence_id,
        capability="image",
        status="success",
        facts=safe_facts,
        weak_signals=[],
        source_refs=source_refs,
        verifier_version=_VERIFIER_VERSION,
        started_at=started_at,
        completed_at=utc_now(),
    )


def _failure(
    evidence_id: str,
    started_at: object,
    *,
    status: str,
    media_status: MediaVerificationStatus,
    error_code: str,
    safe_error_message: str,
    visual_diagnostics: dict[str, object] | None = None,
) -> VerificationObservation:
    safe_facts: dict[str, object] = {"media_status": media_status}
    if visual_diagnostics is not None:
        safe_facts["visual_diagnostics"] = visual_diagnostics
    return VerificationObservation.from_text(
        safe_error_message,
        evidence_id=evidence_id,
        capability="image",
        status=status,
        facts=safe_facts,
        weak_signals=[],
        source_refs=[evidence_id],
        verifier_version=_VERIFIER_VERSION,
        started_at=started_at,
        completed_at=utc_now(),
        error_code=error_code,
        safe_error_message=safe_error_message,
    )


class FocusProofMediaEvidenceVerificationTool(
    ToolDefinition[EvidenceReferenceAction, VerificationObservation]
):
    name: ClassVar[str] = "focusproof_media_evidence_verification"

    @classmethod
    def annotations_for_focusproof(cls) -> ToolAnnotations:
        return read_only_annotations("FocusProof media evidence verification")

    @classmethod
    def create(
        cls,
        conv_state: Any | None = None,
        *,
        session_id: str,
        repository: ScopedMediaEvidenceRepository | None = None,
    ) -> Sequence[Self]:
        del conv_state
        return [
            cls(
                description=(
                    "Inspect authoritative verified media facts and pixel-grounded "
                    "visual facts by evidence_id. Only evidence_id is accepted; never "
                    "provide image bytes, paths, object keys, owner IDs, or raw fields."
                ),
                action_type=EvidenceReferenceAction,
                observation_type=VerificationObservation,
                executor=MediaEvidenceVerificationExecutor(repository, session_id),
                annotations=cls.annotations_for_focusproof(),
            )
        ]
