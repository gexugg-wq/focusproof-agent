from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable, Iterator, Sequence
from typing import Any, TypeAlias

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openhands.sdk.llm import LLM

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-server"))

from focusproof.api.app import create_app  # noqa: E402
from focusproof.config.env import load_project_env  # noqa: E402

CANONICAL_IMAGE_PATH = (
    ROOT / "agent-server/tests/fixtures/real-vision/focusproof-general-session.png"
).resolve()
CANONICAL_IMAGE_SHA256 = "9a2fc6ac6864101e14e933e503840705392f5153fd2a4b2b7b9da246aeac4e67"
CANONICAL_IMAGE_SIZE = 66594
CANONICAL_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MEDIA_VERIFICATION_TOOL_NAME = "focusproof_media_evidence_verification"


class GateBlocked(RuntimeError):
    pass


class GateFailed(RuntimeError):
    pass


class GateDiagnosticFailure(GateFailed):
    def __init__(self, message: str, diagnostics: dict[str, object]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


ReportSchema: TypeAlias = type | dict[str, "ReportSchema"] | list["ReportSchema"]
_ASSURANCE: dict[str, str | bool] = {
    "mediaScanner": "fake-clean",
    "scope": "local-test-only",
    "productionMalwareScanningVerified": False,
}
_PASS_RESULT_SCHEMA: dict[str, ReportSchema] = {
    "status": str,
    "provider": str,
    "model": str,
    "scanner_mode": str,
    "sdk": {"name": str, "version": str},
    "image": {"path": str, "size": int, "sha256": str},
    "conversationId": str,
    "eventTypes": [str],
    "nativeEvents": {"actionCount": int, "observationCount": int},
    "checks": {
        "imagePayloadNotPersisted": bool,
        "officialConversationUsed": bool,
        "realRuntimeUsed": bool,
        "visionActive": bool,
        "productionLlmUsed": bool,
        "nativeActionObserved": bool,
        "nativeObservationObserved": bool,
        "mediaToolUsed": bool,
        "monadDisabled": bool,
    },
    "limits": {
        "maxCallsPerReview": int,
        "maxReviewSeconds": int,
        "maxConcurrentReviews": int,
    },
    "assurance": {
        "mediaScanner": str,
        "scope": str,
        "productionMalwareScanningVerified": bool,
    },
}
_FAILURE_RESULT_SCHEMA: dict[str, ReportSchema] = {
    "status": str,
    "reason": str,
    "assurance": {
        "mediaScanner": str,
        "scope": str,
        "productionMalwareScanningVerified": bool,
    },
}


def _completed_review_result(review: dict[str, Any]) -> tuple[int, str]:
    if review.get("reviewStatus") != "completed":
        raise GateFailed("completed review contract failed")
    review_result = review.get("reviewResult")
    if not isinstance(review_result, dict):
        raise GateFailed("completed review contract failed")
    score = review_result.get("score")
    summary = review_result.get("summary")
    if type(score) is not int or not isinstance(summary, str) or not summary.strip():
        raise GateFailed("completed review contract failed")
    return score, summary


def _review_state_action(review: dict[str, Any]) -> str:
    status = review.get("reviewStatus")
    if status == "completed":
        _completed_review_result(review)
        return "completed"
    if status == "awaiting_user":
        return "awaiting_user"
    raise GateFailed("review state contract failed")


def _provider_environment(
    scanner_mode: str, provider: str = "openai", model: str = "qwen3.7-plus"
) -> tuple[dict[str, str], str]:
    values = load_project_env(ROOT)
    key = values.get("DASHSCOPE_API_KEY") or values.get("OPENAI_API_KEY")
    base_url = values.get("DASHSCOPE_BASE_URL") or values.get("OPENAI_BASE_URL")
    if not key or not base_url:
        raise GateBlocked("real provider configuration is unavailable")
    qualified_model = f"{provider}/{model}"
    configured = {
        "FOCUSPROOF_PROFILE": "demo-real-vision",
        "FOCUSPROOF_MEDIA_ENABLED": "true",
        "FOCUSPROOF_MEDIA_SCANNER_MODE": scanner_mode,
        "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "false",
        "FOCUSPROOF_LLM_PROVIDER": provider,
        "FOCUSPROOF_LLM_MODEL": qualified_model,
        "FOCUSPROOF_LLM_SUPPORTS_VISION": "true",
        "FOCUSPROOF_LLM_BASE_URL": base_url,
        "FOCUSPROOF_LLM_API_KEY": key,
        "FOCUSPROOF_LLM_REQUEST_TIMEOUT_SECONDS": "60",
        "FOCUSPROOF_LLM_NUM_RETRIES": "0",
        "FOCUSPROOF_LLM_RETRY_MIN_WAIT_SECONDS": "0",
        "FOCUSPROOF_LLM_RETRY_MAX_WAIT_SECONDS": "0",
        "FOCUSPROOF_LLM_CONTEXT_WINDOW_TOKENS": "65536",
        "FOCUSPROOF_LLM_MAX_OUTPUT_TOKENS": "4096",
        "FOCUSPROOF_LLM_MAX_ITERATIONS": "6",
        "FOCUSPROOF_LLM_MAX_REVIEW_SECONDS": "120",
        "FOCUSPROOF_LLM_MAX_CONCURRENT_REVIEWS": "1",
        "FOCUSPROOF_LLM_ADMISSION_TIMEOUT_SECONDS": "2",
        "FOCUSPROOF_LLM_MAX_CALLS_PER_REVIEW": "6",
        "FOCUSPROOF_LLM_MAX_COST_USD": "1",
        "FOCUSPROOF_LLM_INPUT_COST_PER_TOKEN": "0",
        "FOCUSPROOF_LLM_OUTPUT_COST_PER_TOKEN": "0",
        "LITELLM_LOCAL_MODEL_COST_MAP": "true",
    }
    return configured, qualified_model


@contextmanager
def _temporary_environment(configured: dict[str, str]) -> Iterator[None]:
    missing = object()
    previous: dict[str, str | object] = {name: os.environ.get(name, missing) for name in configured}
    os.environ.update(configured)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is missing:
                os.environ.pop(name, None)
            else:
                os.environ[name] = str(value)


def _resolve_image(path: Path) -> Path:
    if path.is_symlink() or path.absolute() != path or path != Path(os.path.normpath(path)):
        raise GateBlocked("image must use the canonical lexical path")
    try:
        resolved = path.resolve(strict=True)
        payload = resolved.read_bytes()
    except OSError as exc:
        raise GateBlocked("image must be the canonical real PNG evidence file") from exc
    if (
        resolved != CANONICAL_IMAGE_PATH
        or not resolved.is_file()
        or len(payload) != CANONICAL_IMAGE_SIZE
        or not payload.startswith(CANONICAL_PNG_SIGNATURE)
        or hashlib.sha256(payload).hexdigest() != CANONICAL_IMAGE_SHA256
    ):
        raise GateBlocked("image must be the canonical real PNG evidence file")
    return resolved


def _assurance(scanner_mode: str) -> dict[str, str | bool]:
    return {**_ASSURANCE, "mediaScanner": scanner_mode}


def _empty_failure_diagnostics() -> dict[str, object]:
    return {
        "providerAttempted": False,
        "responseReceived": False,
        "completionSucceeded": False,
        "visualFactsCount": 0,
        "transportOutcome": "unknown",
        "reviewStatus": "not_run",
    }


def _failure_diagnostics(
    *,
    provider_attempted: bool = False,
    transport_outcome: str = "unknown",
    review_status: str = "not_run",
) -> dict[str, object]:
    value = _empty_failure_diagnostics()
    value.update(
        providerAttempted=provider_attempted,
        transportOutcome=transport_outcome,
        reviewStatus=review_status,
    )
    return value


def _infer_transport_outcome(diagnostics: dict[str, object]) -> str:
    if not diagnostics.get("providerAttempted"):
        return "unknown"
    if not diagnostics.get("responseReceived"):
        return "failed"
    if not diagnostics.get("completionSucceeded"):
        return "invalid"
    return "received"


def _production_llm_used(llm: object) -> bool:
    from openhands.sdk.testing import TestLLM

    return not isinstance(llm, TestLLM)


def _completion_response_is_valid(response: object) -> bool:
    if not isinstance(response, dict):
        message = getattr(response, "message", None)
        if message is None:
            return False
        return bool(getattr(message, "content", None)) or bool(getattr(message, "tool_calls", None))
    choices = response.get("choices")
    return isinstance(choices, list) and bool(choices)


def _safe_visual_fact_count(value: object) -> int:
    if not isinstance(value, list):
        return 0
    return len([item for item in value if isinstance(item, str) and item.strip()])


def _safe_event_count_value(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _safe_native_event_entry(event: object) -> dict[str, object] | None:
    if isinstance(event, dict):
        entry = {key: event[key] for key in ("eventType", "id") if key in event}
        event_type = entry.get("eventType")
        if event_type == "ActionEvent":
            for key in ("toolName", "actionClass"):
                if key in event:
                    entry[key] = event[key]
        elif event_type == "ObservationEvent":
            for key in (
                "toolName",
                "observationClass",
                "status",
                "capability",
                "visualFactsCount",
                "errorCode",
            ):
                if key in event:
                    entry[key] = event[key]
        return entry if "eventType" in entry and "id" in entry else None

    event_type = type(event).__name__
    event_id = getattr(event, "id", None)
    if not isinstance(event_id, str) or not event_id:
        return None
    if event_type == "MessageEvent":
        return {"eventType": event_type, "id": event_id}
    if event_type == "ActionEvent":
        action_entry: dict[str, object] = {"eventType": event_type, "id": event_id}
        tool_name = getattr(event, "tool_name", None)
        if isinstance(tool_name, str) and tool_name:
            action_entry["toolName"] = tool_name
        action = getattr(event, "action", None)
        if action is not None:
            action_entry["actionClass"] = type(action).__name__
        return action_entry
    if event_type != "ObservationEvent":
        return None

    observation = getattr(event, "observation", None)
    facts = getattr(observation, "facts", None)
    capability = getattr(observation, "capability", None)
    if not isinstance(capability, str) and isinstance(facts, dict):
        capability = facts.get("capability")
    visual_facts = facts.get("visual_facts") if isinstance(facts, dict) else None
    entry = {
        "eventType": event_type,
        "id": event_id,
        "toolName": str(getattr(event, "tool_name", "") or ""),
        "observationClass": type(observation).__name__ if observation is not None else "",
        "status": str(getattr(observation, "status", "") or ""),
        "capability": str(capability or ""),
        "visualFactsCount": _safe_visual_fact_count(visual_facts),
        "errorCode": str(getattr(observation, "error_code", "") or ""),
    }
    return entry


def _safe_native_event_entries(events: Sequence[object]) -> list[dict[str, object]]:
    safe_events: list[dict[str, object]] = []
    for event in events[:256]:
        safe_event = _safe_native_event_entry(event)
        if safe_event is not None:
            safe_events.append(safe_event)
    return safe_events


def _observe_completion(completion: Any) -> dict[str, object]:
    state: dict[str, object] = {
        "attempts": 1,
        "responseReceived": False,
        "completionSucceeded": False,
    }
    try:
        response = completion()
    except Exception:
        return state
    if response is not None:
        state["responseReceived"] = True
        state["completionSucceeded"] = _completion_response_is_valid(response)
    return state


class CompletionObserver:
    def __init__(self) -> None:
        self.total_attempts = 0
        self.last: dict[str, object] = {
            "attempts": 0,
            "responseReceived": False,
            "completionSucceeded": False,
        }

    def observe(self, completion: Any) -> object:
        self.total_attempts += 1
        try:
            response = completion()
        except Exception:
            self.last = {
                "attempts": 1,
                "responseReceived": False,
                "completionSucceeded": False,
            }
            raise
        self.last = {
            "attempts": 1,
            "responseReceived": response is not None,
            "completionSucceeded": _completion_response_is_valid(response),
        }
        return response


@contextmanager
def _observe_completion_boundary(
    llm: object, observer: CompletionObserver | None = None
) -> Iterator[dict[str, object]]:
    original = getattr(llm, "completion")
    state: dict[str, object] = {
        "attempts": 0,
        "responseReceived": False,
        "completionSucceeded": False,
    }

    def observed(*args: object, **kwargs: object) -> object:
        attempts = state["attempts"]
        if not isinstance(attempts, int):
            raise GateFailed("completion observer state is invalid")
        state["attempts"] = attempts + 1
        try:
            if observer is None:
                response = original(*args, **kwargs)
            else:
                response = observer.observe(lambda: original(*args, **kwargs))
        except Exception:
            state.update(responseReceived=False, completionSucceeded=False)
            raise
        state["responseReceived"] = response is not None
        state["completionSucceeded"] = _completion_response_is_valid(response)
        return response

    object.__setattr__(llm, "completion", observed)
    try:
        yield state
    finally:
        object.__setattr__(llm, "completion", original)


def _agent_decision_count(*, total: int, visual: int | None) -> int | None:
    if visual is None or visual > total:
        return None
    return total - visual


def _runtime_diagnostics(
    events: Sequence[object], *, provider: str = "", model: str = ""
) -> dict[str, object]:
    safe_events = _safe_native_event_entries(events)
    counts = {"messageCount": 0, "actionCount": 0, "observationCount": 0}
    media = {
        "toolName": "",
        "observationClass": "",
        "status": "",
        "capability": "",
        "visualFactsCount": 0,
        "errorCode": "",
    }
    for event in safe_events:
        kind = event.get("eventType")
        if kind == "MessageEvent":
            counts["messageCount"] += 1
        elif kind == "ActionEvent":
            counts["actionCount"] += 1
        elif kind == "ObservationEvent":
            counts["observationCount"] += 1
            if (
                event.get("toolName") == _MEDIA_VERIFICATION_TOOL_NAME
                or event.get("capability") == "image"
            ):
                visual_facts_count = _safe_event_count_value(event.get("visualFactsCount"))
                media = {
                    "toolName": str(event.get("toolName", "")),
                    "observationClass": str(event.get("observationClass", "")),
                    "status": str(event.get("status", "")),
                    "capability": str(event.get("capability", "")),
                    "visualFactsCount": visual_facts_count,
                    "errorCode": str(event.get("errorCode", "")),
                }
    return {
        "officialEventCounts": counts,
        "mediaObservation": media,
        "nativeEventSummary": safe_events,
        "visualProvider": {"provider": provider, "model": model},
    }


def _product_failure_diagnostics(
    handle: object,
    completion_state: dict[str, object],
    completion_observer: CompletionObserver,
    *,
    provider: str,
    model: str,
    review_status: str,
) -> dict[str, object]:
    conversation = getattr(handle, "conversation", None)
    state = getattr(conversation, "state", None)
    events = list(getattr(state, "events", ()))
    provider_attempted = bool(completion_observer.total_attempts)
    if not provider_attempted:
        attempts = completion_state.get("attempts")
        provider_attempted = isinstance(attempts, int) and attempts > 0
    runtime = _runtime_diagnostics(events, provider=provider, model=model)
    diagnostics = {
        **runtime,
        **completion_state,
    }
    diagnostics["providerAttempted"] = provider_attempted
    diagnostics["transportOutcome"] = _infer_transport_outcome(
        {
            **completion_state,
            "providerAttempted": provider_attempted,
        }
    )
    media_observation = runtime["mediaObservation"]
    visual_facts_count = (
        media_observation.get("visualFactsCount", 0) if isinstance(media_observation, dict) else 0
    )
    diagnostics["visualFactsCount"] = visual_facts_count
    diagnostics["totalProviderCompletionCalls"] = completion_observer.total_attempts
    diagnostics["reviewStatus"] = review_status
    diagnostics["visualFactsParsed"] = visual_facts_count >= 3
    return diagnostics


def _require_product_success(result: dict[str, object]) -> None:
    diagnostics = result.get("diagnostics")
    v3_summary = result.get("v3Summary")
    eventlog_summary = result.get("eventlogSummary")
    checks = result.get("checks")
    failure_diagnostics = (
        dict(diagnostics) if isinstance(diagnostics, dict) else _empty_failure_diagnostics()
    )
    try:
        if not isinstance(v3_summary, dict) or not isinstance(eventlog_summary, dict):
            raise GateFailed("complete audit summaries are required")
        _validate_v3_summary(v3_summary)
        _validate_safe_eventlog_summary(eventlog_summary)
    except GateFailed as exc:
        raise GateDiagnosticFailure(str(exc), failure_diagnostics) from exc

    visual_facts_count = (
        diagnostics.get("visualFactsCount") if isinstance(diagnostics, dict) else None
    )
    attempts = diagnostics.get("attempts") if isinstance(diagnostics, dict) else None
    attribution = diagnostics.get("visualProvider") if isinstance(diagnostics, dict) else None
    expected_attribution = {
        "provider": result.get("expectedProvider"),
        "model": result.get("expectedModel"),
    }
    expected_production_llm = result.get("expectedProductionLlmUsed")
    if (
        result.get("reviewStatus") != "completed"
        or not isinstance(diagnostics, dict)
        or diagnostics.get("reviewStatus") != "completed"
        or diagnostics.get("completionSucceeded") is not True
        or diagnostics.get("responseReceived") is not True
        or type(attempts) is not int
        or attempts < 1
        or diagnostics.get("providerAttempted") is not True
        or diagnostics.get("transportOutcome") != "received"
        or type(visual_facts_count) is not int
        or visual_facts_count < 3
        or diagnostics.get("visualFactsParsed") is not True
        or attribution != expected_attribution
        or not isinstance(checks, dict)
        or type(expected_production_llm) is not bool
        or checks.get("productionLlmUsed") is not expected_production_llm
        or eventlog_summary.get("diagnostics") != diagnostics
    ):
        raise GateDiagnosticFailure(
            "product chain did not produce complete visual evidence",
            failure_diagnostics,
        )


def _migrate(database_url: str) -> None:
    alembic = Config(ROOT / "alembic.ini")
    alembic.set_main_option("script_location", str(ROOT / "agent-server" / "migrations"))
    alembic.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic, "head")


def _run_product_chain(
    image_path: Path,
    provider: str,
    model: str,
    scanner_mode: str,
    *,
    llm_factory: Callable[[str], LLM] | None = None,
    allow_test_llm: bool = False,
) -> dict[str, Any]:
    image_bytes = image_path.read_bytes()
    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    with tempfile.TemporaryDirectory(prefix="focusproof-real-image-gate-") as raw:
        data_dir = Path(raw) / "data"
        data_dir.mkdir()
        database_url = f"sqlite+pysqlite:///{data_dir / 'focusproof.db'}"
        _migrate(database_url)
        app = create_app(
            database_url=database_url,
            data_dir=data_dir,
            review_timeout_seconds=120,
            llm_factory=llm_factory,
        )
        with TestClient(app) as client:
            created = client.post(
                "/sessions",
                json={
                    "domain": "general",
                    "title": "Visual learning evidence",
                    "goal": (
                        "Inspect the submitted screenshot and explain the visible "
                        "evidence grounded directly in its pixels."
                    ),
                    "expectedOutput": "A concise, image-grounded explanation.",
                    "plannedMinutes": 5,
                },
            )
            if created.status_code != 200:
                raise GateFailed("session creation failed")
            session_id = str(created.json()["sessionId"])
            upload = client.post(
                f"/sessions/{session_id}/evidence/image",
                files={
                    "file": (
                        image_path.name,
                        image_bytes,
                        "image/png",
                    )
                },
                data={
                    "explanation": ("This screenshot is the canonical real PNG gate evidence."),
                    "idempotency_key": "real-image-provider-gate",
                },
            )
            if upload.status_code != 200:
                raise GateFailed("image upload failed")
            manager = app.state.conversation_manager
            handle = manager.get(session_id)
            llm = handle.conversation.agent.llm
            completion_observer = CompletionObserver()
            responses: list[dict[str, Any]] = []
            with _observe_completion_boundary(llm, completion_observer) as completion_state:
                for _ in range(3):
                    response = client.post(f"/sessions/{session_id}/review")
                    if response.status_code != 200:
                        payload = response.json()
                        error_code = payload.get("error") if isinstance(payload, dict) else None
                        raise GateDiagnosticFailure(
                            f"review request failed: http-{response.status_code} "
                            f"error={error_code or 'unknown'}",
                            _product_failure_diagnostics(
                                handle,
                                completion_state,
                                completion_observer,
                                provider=provider,
                                model=model,
                                review_status=f"http-{response.status_code}",
                            ),
                        )
                    review = response.json()
                    responses.append(review)
                    review_action = _review_state_action(review)
                    if review_action == "completed":
                        break
                    questions = review.get("agentQuestions") or []
                    if not questions:
                        raise GateFailed("review neither completed nor requested input")
                    question = questions[0]
                    answer = client.post(
                        f"/sessions/{session_id}/answer",
                        json={
                            "questionId": question["questionId"],
                            "answer": (
                                "The image itself is the authoritative source. Inspect "
                                "its visible pixels directly."
                            ),
                        },
                    )
                    if answer.status_code != 200:
                        raise GateFailed("learner answer failed")
            if not responses or responses[-1].get("reviewStatus") != "completed":
                raise GateFailed("review did not complete within the interaction bound")

            events = list(handle.conversation.state.events)
            serialized_events = [event.model_dump_json() for event in events]
            event_types = sorted({type(event).__name__ for event in events})
            all_native = "\n".join(serialized_events)
            session = client.get(f"/sessions/{session_id}").json()
            plugin_capabilities = session.get("view", {}).get("pluginCapabilities", [])
            production_llm_used = _production_llm_used(llm)
            checks = {
                "imagePayloadNotPersisted": "data:image/png;base64," not in all_native,
                "officialConversationUsed": responses[-1].get("usedOpenHandsConversation") is True,
                "realRuntimeUsed": responses[-1].get("conversationMode") == "openhands-local-real",
                "visionActive": bool(llm.vision_is_active()),
                "productionLlmUsed": production_llm_used,
                "nativeActionObserved": int(responses[-1].get("actionEventsCount") or 0) > 0,
                "nativeObservationObserved": int(responses[-1].get("observationEventsCount") or 0)
                > 0,
                "mediaToolUsed": "focusproof_media_evidence_verification" in all_native,
                "monadDisabled": not any(
                    str(item.get("pluginId", "")).lower() == "monad"
                    for item in plugin_capabilities
                    if isinstance(item, dict)
                ),
            }
            required_checks = dict(checks)
            if allow_test_llm:
                required_checks.pop("productionLlmUsed")
                required_checks.pop("realRuntimeUsed")
            if not all(required_checks.values()):
                missing = sorted(name for name, passed in checks.items() if not passed)
                raise GateFailed("acceptance checks failed: " + ", ".join(missing))
            diagnostics = _product_failure_diagnostics(
                handle,
                completion_state,
                completion_observer,
                provider=provider,
                model=model,
                review_status=str(responses[-1].get("reviewStatus")),
            )
            eventlog_summary = _safe_eventlog_summary(events, diagnostics)
            try:
                product_lineage = manager.project_safe_completed_review_lineage(session_id)
            except ValueError as exc:
                raise GateFailed("product audit lineage is invalid") from exc
            eventlog_summary.update(schemaVersion="3.0", **product_lineage)
            _require_product_success(
                {
                    "reviewStatus": responses[-1].get("reviewStatus"),
                    "diagnostics": diagnostics,
                    "v3Summary": eventlog_summary,
                    "eventlogSummary": eventlog_summary,
                    "checks": checks,
                    "expectedProvider": provider,
                    "expectedModel": model,
                    "expectedProductionLlmUsed": production_llm_used,
                }
            )
            report = {
                "status": "PASS",
                "provider": provider,
                "model": model,
                "scanner_mode": scanner_mode,
                "sdk": {"name": "openhands-sdk", "version": "1.31.0"},
                "image": {
                    "path": str(image_path),
                    "size": len(image_bytes),
                    "sha256": image_sha256,
                },
                "conversationId": session_id,
                "eventTypes": event_types,
                "nativeEvents": {
                    "actionCount": responses[-1].get("actionEventsCount"),
                    "observationCount": responses[-1].get("observationEventsCount"),
                },
                "checks": checks,
                "limits": {
                    "maxCallsPerReview": 6,
                    "maxReviewSeconds": 120,
                    "maxConcurrentReviews": 1,
                },
                "assurance": _assurance(scanner_mode),
            }
            return {
                "report": report,
                "diagnostics": diagnostics,
                "eventlogSummary": eventlog_summary,
            }


def _validate_report_value(
    value: object,
    schema: ReportSchema,
    known_secrets: frozenset[str],
) -> None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise GateFailed("report schema validation failed")
    if isinstance(schema, type):
        if schema is int:
            valid = type(value) is int
        elif schema is bool:
            valid = type(value) is bool
        else:
            valid = isinstance(value, schema)
        if not valid:
            raise GateFailed("report schema validation failed")
        if isinstance(value, str):
            lowered = value.lower()
            if len(value) > 8192 or "data:" in lowered:
                raise GateFailed("report schema validation failed")
            if any(secret and secret in value for secret in known_secrets):
                raise GateFailed("report schema validation failed")
        return
    if isinstance(schema, list):
        if not isinstance(value, list) or len(schema) != 1:
            raise GateFailed("report schema validation failed")
        for item in value:
            _validate_report_value(item, schema[0], known_secrets)
        return
    if not isinstance(value, dict) or set(value) != set(schema):
        raise GateFailed("report schema validation failed")
    sensitive_keys = ("secret", "api_key", "token", "raw", "base64", "image_content")
    for key, nested_schema in schema.items():
        normalized = key.lower()
        if any(sensitive in normalized for sensitive in sensitive_keys):
            raise GateFailed("report schema validation failed")
        _validate_report_value(value[key], nested_schema, known_secrets)


def _validate_safe_payload(
    value: object,
    *,
    known_secrets: set[str] | frozenset[str] = frozenset(),
    _depth: int = 0,
) -> None:
    if _depth > 12 or isinstance(value, (bytes, bytearray, memoryview)):
        raise GateFailed("unsafe report payload")
    if isinstance(value, str):
        lowered = value.lower()
        raw_markers = (
            "data:image",
            "raw-image",
            "raw pixels",
            "ivborw0kggo",
            "qujdrevgr0hjsktmtq==",
        )
        if len(value) > 8192 or any(marker in lowered for marker in raw_markers):
            raise GateFailed("unsafe report payload")
        if any(secret and secret in value for secret in known_secrets):
            raise GateFailed("unsafe report payload")
        return
    if isinstance(value, list):
        if len(value) > 256:
            raise GateFailed("unsafe report payload")
        for item in value:
            _validate_safe_payload(item, known_secrets=known_secrets, _depth=_depth + 1)
        return
    if isinstance(value, dict):
        forbidden = (
            "unknown",
            "raw",
            "image_content",
            "token",
            "secret",
            "api_key",
            "facttext",
            "message",
        )
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized == "message" or any(marker in normalized for marker in forbidden[:-1]):
                raise GateFailed("unsafe report payload")
            _validate_safe_payload(item, known_secrets=known_secrets, _depth=_depth + 1)
        if len(json.dumps(value, default=str)) > 65536:
            raise GateFailed("unsafe report payload")


def _runner_source_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _atomic_write_text(path: Path, rendered: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_report_pair(
    path: Path,
    result: dict[str, Any],
    *,
    diagnostics: dict[str, object],
    eventlog_summary: dict[str, object] | None = None,
    known_secrets: set[str] | frozenset[str] = frozenset(),
) -> None:
    force_sidecar_failure = diagnostics.pop("forceSidecarFailure", False)
    _validate_safe_payload(diagnostics, known_secrets=known_secrets)
    previous_report = path.read_bytes() if path.exists() else None
    sidecar = path.with_suffix(path.suffix + ".sha256")
    previous_sidecar = sidecar.read_bytes() if sidecar.exists() else None
    status = result.get("status")
    schema = _PASS_RESULT_SCHEMA if status == "PASS" else _FAILURE_RESULT_SCHEMA
    _validate_report_value(result, schema, frozenset(known_secrets))
    assurance = result["assurance"]
    if (
        assurance["mediaScanner"] not in {"fake-clean", "clamd"}
        or assurance["productionMalwareScanningVerified"] is not False
    ):
        raise GateFailed("visual provider report cannot certify malware scanning")
    report = {
        "schemaVersion": "1.0",
        "timestamp": datetime.now(UTC).isoformat(),
        "gate": "real_visual_provider",
        **result,
        "diagnostics": diagnostics,
        "runnerSourceSha256": _runner_source_sha256(),
    }
    if eventlog_summary is not None:
        _validate_safe_payload(eventlog_summary, known_secrets=known_secrets)
        report["eventlogSummary"] = eventlog_summary
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    try:
        _atomic_write_text(path, rendered)
        if force_sidecar_failure:
            raise GateFailed("sidecar publication failed")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        _atomic_write_text(
            sidecar,
            json.dumps({"schemaVersion": "1.0", "reportSha256": digest}, sort_keys=True) + "\n",
        )
    except Exception:
        if previous_report is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous_report)
        if previous_sidecar is None:
            sidecar.unlink(missing_ok=True)
        else:
            sidecar.write_bytes(previous_sidecar)
        raise


def _safe_eventlog_entry(event: dict[str, object]) -> dict[str, object]:
    _validate_safe_payload(event)
    return {
        key: event[key]
        for key in (
            "eventType",
            "id",
            "toolName",
            "actionClass",
            "observationClass",
            "status",
            "capability",
            "visualFactsCount",
            "errorCode",
        )
        if key in event
    }


def _safe_eventlog_summary(
    events: Sequence[object],
    diagnostics: dict[str, object],
    *,
    report_path: Path | None = None,
) -> dict[str, object]:
    safe_events = _safe_native_event_entries(events)
    runtime = _runtime_diagnostics(safe_events)
    summary: dict[str, object] = {
        "schemaVersion": "1.0",
        "eventCounts": runtime["officialEventCounts"],
        "events": safe_events,
        "diagnostics": diagnostics,
        "digest": hashlib.sha256(json.dumps(safe_events, sort_keys=True).encode()).hexdigest(),
    }
    return summary


def _v3_fixture(
    *, mutation: str | None = None, consumed_mutation: str | None = None
) -> dict[str, object]:
    fact_ids = ["fact_" + "a" * 64]
    consumed = list(fact_ids)
    value: dict[str, object] = {
        "schemaVersion": "3.0",
        "projection": "projection_1",
        "source": "obs_1",
        "fact": fact_ids[0],
        "score": "score_1",
        "review": "review_1",
        "review_score": "score_1",
        "review_projection": "projection_1",
        "factIds": fact_ids,
        "consumedFactIds": consumed,
        "consumedFactCount": 1,
        "digest": "b" * 64,
    }
    if mutation:
        if mutation == "wrong_score_event":
            value["review_score"] = "score_2"
        elif mutation == "missing_projection":
            value.pop("projection")
        elif mutation == "extra_projection":
            value["review_projection"] = "projection_2"
        elif mutation == "duplicate_fact_id":
            value["factIds"] = fact_ids * 2
        elif mutation == "conflicting_digest":
            value["digest"] = "c" * 63
        elif mutation == "invalid_digest":
            value["digest"] = "not-a-digest"
        elif mutation == "missing_source_observation":
            value.pop("source")
    if consumed_mutation:
        if consumed_mutation == "missing":
            value["consumedFactIds"] = []
        elif consumed_mutation == "extra":
            value["consumedFactIds"] = consumed + ["fact_" + "c" * 64]
        elif consumed_mutation == "different":
            value["consumedFactIds"] = ["fact_" + "d" * 64]
        elif consumed_mutation == "duplicate":
            value["consumedFactIds"] = consumed * 2
    return value


def _validate_v3_summary(value: dict[str, object]) -> None:
    required_ids = (
        "projection",
        "source",
        "fact",
        "score",
        "review",
        "review_score",
        "review_projection",
    )
    if value.get("schemaVersion") != "3.0":
        raise GateFailed("v3 lineage required")
    for key in required_ids:
        item = value.get(key)
        if not isinstance(item, str) or not item or item != item.strip():
            raise GateFailed("invalid lineage id")
    facts = value.get("factIds")
    consumed = value.get("consumedFactIds")
    if (
        not isinstance(facts, list)
        or not isinstance(consumed, list)
        or any(not isinstance(item, str) or not item or item != item.strip() for item in facts)
        or any(not isinstance(item, str) or not item or item != item.strip() for item in consumed)
        or len(set(facts)) != len(facts)
    ):
        raise GateFailed("invalid fact lineage")
    if (
        set(facts) != set(consumed)
        or len(consumed) != value.get("consumedFactCount")
        or len(set(consumed)) != len(consumed)
    ):
        raise GateFailed("invalid consumed facts")
    if value["score"] != value["review_score"] or value["projection"] != value["review_projection"]:
        raise GateFailed("ambiguous lineage")
    projection_ids = value.get("projectionIds")
    source_ids = value.get("sourceObservationEventIds")
    review_projection_ids = value.get("reviewProjectionIds")
    lineage = value.get("lineage")
    audit_events = value.get("auditEvents")
    extended = (projection_ids, source_ids, review_projection_ids, lineage, audit_events)
    if any(item is not None for item in extended):
        if (
            not isinstance(projection_ids, list)
            or not isinstance(source_ids, list)
            or not isinstance(review_projection_ids, list)
            or not isinstance(lineage, list)
            or not isinstance(audit_events, list)
            or not projection_ids
            or len(projection_ids) != len(source_ids)
            or len(projection_ids) != len(lineage)
            or len(set(projection_ids)) != len(projection_ids)
            or len(set(source_ids)) != len(source_ids)
            or any(
                not isinstance(item, str) or not item or item != item.strip()
                for item in projection_ids
            )
            or any(
                not isinstance(item, str) or not item or item != item.strip() for item in source_ids
            )
            or review_projection_ids != projection_ids
            or value["projection"] != projection_ids[0]
            or value["source"] != source_ids[0]
            or value["review_projection"] != review_projection_ids[0]
        ):
            raise GateFailed("invalid extended lineage")
        projected_fact_ids: list[str] = []
        for index, item in enumerate(lineage):
            if not isinstance(item, dict):
                raise GateFailed("invalid extended lineage")
            lineage_facts = item.get("facts")
            lineage_consumed = item.get("consumedFactIds")
            if (
                item.get("projectionId") != projection_ids[index]
                or item.get("sourceObservationEventId") != source_ids[index]
                or not isinstance(lineage_facts, list)
                or not isinstance(lineage_consumed, list)
                or item.get("factCount") != len(lineage_facts)
            ):
                raise GateFailed("invalid extended lineage")
            safe_fact_ids: list[str] = []
            for fact_item in lineage_facts:
                if not isinstance(fact_item, dict):
                    raise GateFailed("invalid extended lineage")
                fact_id = fact_item.get("factId")
                fact_type = fact_item.get("factType")
                if (
                    not isinstance(fact_id, str)
                    or not fact_id
                    or fact_id != fact_id.strip()
                    or not isinstance(fact_type, str)
                    or not fact_type
                    or fact_type != fact_type.strip()
                    or set(fact_item) != {"factId", "factType"}
                ):
                    raise GateFailed("invalid extended lineage")
                safe_fact_ids.append(fact_id)
            if safe_fact_ids != lineage_consumed:
                raise GateFailed("invalid extended lineage")
            projected_fact_ids.extend(safe_fact_ids)
        if projected_fact_ids != facts:
            raise GateFailed("invalid extended lineage")
        expected_audit_events = [
            {"eventType": "score.calculated", "id": value["score"]},
            {"eventType": "review.completed", "id": value["review"]},
        ]
        if audit_events != expected_audit_events:
            raise GateFailed("invalid audit event lineage")
    digest = value.get("digest")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(ch not in "0123456789abcdef" for ch in digest)
    ):
        raise GateFailed("invalid digest")


def _validate_safe_eventlog_summary(value: dict[str, object]) -> None:
    _validate_safe_payload(value)
    events = value.get("events")
    counts = value.get("eventCounts")
    diagnostics = value.get("diagnostics")
    digest = value.get("digest")
    if (
        value.get("schemaVersion") != "3.0"
        or not isinstance(events, list)
        or not isinstance(counts, dict)
        or set(counts) != {"messageCount", "actionCount", "observationCount"}
        or any(type(count) is not int or count < 0 for count in counts.values())
        or not isinstance(diagnostics, dict)
    ):
        raise GateFailed("invalid safe EventLog summary")
    allowed_event_keys = {
        "eventType",
        "id",
        "toolName",
        "actionClass",
        "observationClass",
        "status",
        "capability",
        "visualFactsCount",
        "errorCode",
    }
    for event in events:
        if (
            not isinstance(event, dict)
            or not set(event).issubset(allowed_event_keys)
            or not isinstance(event.get("eventType"), str)
            or not isinstance(event.get("id"), str)
            or not event["id"]
        ):
            raise GateFailed("invalid safe EventLog entry")
        if "visualFactsCount" in event and (
            type(event["visualFactsCount"]) is not int or event["visualFactsCount"] < 0
        ):
            raise GateFailed("invalid safe EventLog entry")
    actual_counts = _runtime_diagnostics(events)["officialEventCounts"]
    expected_digest = hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()
    if counts != actual_counts or digest != expected_digest:
        raise GateFailed("invalid safe EventLog summary")


def _v3_fixture_sha256() -> str:
    return hashlib.sha256(json.dumps(_v3_fixture(), sort_keys=True).encode()).hexdigest()


def _write_report(
    path: Path,
    result: dict[str, Any],
    *,
    known_secrets: set[str] | frozenset[str] = frozenset(),
) -> None:
    status = result.get("status")
    schema = _PASS_RESULT_SCHEMA if status == "PASS" else _FAILURE_RESULT_SCHEMA
    if status not in {"PASS", "BLOCKED", "FAIL"}:
        raise GateFailed("report schema validation failed")
    _validate_report_value(result, schema, frozenset(known_secrets))
    if (
        result["assurance"]["scope"] != "local-test-only"
        or result["assurance"]["mediaScanner"] not in {"fake-clean", "clamd"}
        or result["assurance"]["productionMalwareScanningVerified"] is not False
    ):
        raise GateFailed("report schema validation failed")
    report = {
        "schemaVersion": "1.0",
        "timestamp": datetime.now(UTC).isoformat(),
        "gate": "real_visual_provider",
        **result,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--execute-real-provider",
        action="store_true",
        help="Explicitly authorize the single real visual-provider gate run.",
    )
    parser.add_argument(
        "--scanner-mode",
        choices=("fake-clean", "clamd"),
        required=True,
        help="Explicit scanner mode; fake-clean is test/local-only.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if platform.system().lower() != "linux" or sys.version_info[:2] != (3, 12):
        print("Real image provider gate requires Linux/Python 3.12", file=sys.stderr)
        return 2
    known_secrets: set[str] = set()
    if not args.execute_real_provider:
        _publish_report_pair(
            args.report,
            {
                "status": "BLOCKED",
                "reason": "real visual provider execution was not explicitly enabled",
                "assurance": _assurance(args.scanner_mode),
            },
            diagnostics=_empty_failure_diagnostics(),
        )
        return 2
    try:
        image_path = _resolve_image(args.image)
        configured, _qualified_model = _provider_environment(
            args.scanner_mode, args.provider, args.model
        )
        known_secrets.add(configured["FOCUSPROOF_LLM_API_KEY"])
        with _temporary_environment(configured):
            product_result = _run_product_chain(
                image_path, args.provider, args.model, args.scanner_mode
            )
        _publish_report_pair(
            args.report,
            product_result["report"],
            diagnostics=product_result["diagnostics"],
            eventlog_summary=product_result["eventlogSummary"],
            known_secrets=known_secrets,
        )
        print(json.dumps({"status": "PASS", "report": str(args.report)}))
        return 0
    except GateBlocked as exc:
        _publish_report_pair(
            args.report,
            {
                "status": "BLOCKED",
                "reason": str(exc),
                "assurance": _assurance(args.scanner_mode),
            },
            diagnostics=_empty_failure_diagnostics(),
            known_secrets=known_secrets,
        )
        return 2
    except GateDiagnosticFailure as exc:
        _publish_report_pair(
            args.report,
            {"status": "FAIL", "reason": str(exc), "assurance": _assurance(args.scanner_mode)},
            diagnostics=exc.diagnostics,
            known_secrets=known_secrets,
        )
        return 1
    except GateFailed as exc:
        _publish_report_pair(
            args.report,
            {
                "status": "FAIL",
                "reason": str(exc),
                "assurance": _assurance(args.scanner_mode),
            },
            diagnostics=_empty_failure_diagnostics(),
            known_secrets=known_secrets,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
