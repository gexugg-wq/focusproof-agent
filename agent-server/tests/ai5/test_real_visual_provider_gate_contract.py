from __future__ import annotations

import hashlib
import inspect
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import MessageToolCall, TextContent

from focusproof.openhands_runtime.tools.verification import (
    EvidenceReferenceAction,
    VerificationObservation,
)
from scripts import run_real_visual_provider_gate as gate


def _completed_review(score: int = 80, summary: str = "grounded") -> dict[str, object]:
    return {"reviewStatus": "completed", "reviewResult": {"score": score, "summary": summary}}


def test_completed_review_contract_reads_nested_score_and_summary() -> None:
    assert gate._completed_review_result(_completed_review()) == (80, "grounded")


@pytest.mark.parametrize(
    "review",
    [
        {"reviewStatus": "awaiting_user", "reviewResult": {"score": 80, "summary": "x"}},
        {"reviewStatus": "completed", "reviewResult": None},
        {"reviewStatus": "completed", "reviewResult": []},
        {"reviewStatus": "completed", "reviewResult": "not-an-object"},
        {"reviewStatus": "completed", "reviewResult": {"score": True, "summary": "x"}},
        {"reviewStatus": "completed", "reviewResult": {"score": 80.0, "summary": "x"}},
        {"reviewStatus": "completed", "reviewResult": {"score": 80, "summary": ""}},
        {"reviewStatus": "completed", "reviewResult": {"score": 80, "summary": "   "}},
        {"reviewStatus": "completed", "score": 80, "summary": "top level only"},
    ],
)
def test_completed_review_contract_rejects_missing_or_invalid_nested_result(
    review: dict[str, object],
) -> None:
    with pytest.raises(gate.GateFailed):
        gate._completed_review_result(review)


def test_review_state_completed_validates_result_immediately() -> None:
    assert gate._review_state_action(_completed_review()) == "completed"
    with pytest.raises(gate.GateFailed):
        gate._review_state_action({"reviewStatus": "completed", "reviewResult": None})


def test_review_state_only_awaiting_user_may_continue() -> None:
    assert gate._review_state_action({"reviewStatus": "awaiting_user"}) == "awaiting_user"


@pytest.mark.parametrize(
    "review",
    [
        {"reviewStatus": "failed", "agentQuestions": [{"questionId": "q"}]},
        {"agentQuestions": [{"questionId": "q"}]},
        {"reviewStatus": "unknown", "agentQuestions": [{"questionId": "q"}]},
        {"reviewStatus": None, "agentQuestions": [{"questionId": "q"}]},
        {"reviewStatus": "", "agentQuestions": [{"questionId": "q"}]},
        {"reviewStatus": "completed", "reviewResult": {"score": True, "summary": "invalid"}},
        {"reviewStatus": "completed", "reviewResult": {"score": 80, "summary": "   "}},
    ],
)
def test_review_state_rejects_terminal_missing_unknown_or_invalid_completed(
    review: dict[str, object],
) -> None:
    with pytest.raises(gate.GateFailed):
        gate._review_state_action(review)


def test_real_image_gate_cli_rejects_missing_scanner_mode() -> None:
    with pytest.raises(SystemExit):
        gate._parse_args(["--report", "r", "--image", "i", "--provider", "openai", "--model", "m"])


@pytest.mark.parametrize(
    ("missing", "arguments"),
    [
        ("image", ["--provider", "openai", "--model", "m"]),
        ("provider", ["--image", "i", "--model", "m"]),
        ("model", ["--image", "i", "--provider", "openai"]),
    ],
)
def test_real_image_gate_cli_requires_explicit_real_inputs(
    missing: str, arguments: list[str]
) -> None:
    with pytest.raises(SystemExit):
        gate._parse_args(["--report", "r", "--scanner-mode", "fake-clean", *arguments])
    assert missing not in arguments


@pytest.mark.parametrize("scanner_mode", ["fake-clean", "clamd"])
def test_real_image_gate_cli_rejects_unlocked_runtime_flags(scanner_mode: str) -> None:
    args = gate._parse_args(
        [
            "--report",
            "r",
            "--image",
            "i",
            "--provider",
            "openai",
            "--model",
            "m",
            "--scanner-mode",
            scanner_mode,
        ]
    )
    assert args.scanner_mode == scanner_mode
    assert not hasattr(args, "max_calls")


def test_real_image_gate_cli_rejects_non_canonical_lexical_path_before_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def provider(*args: object, **kwargs: object) -> tuple[dict[str, str], str]:
        nonlocal called
        called = True
        return {}, "x"

    monkeypatch.setattr(gate, "_provider_environment", provider)
    assert (
        gate.main(
            [
                "--execute-real-provider",
                "--report",
                str(tmp_path / "r.json"),
                "--image",
                str(tmp_path / "x.png"),
                "--provider",
                "openai",
                "--model",
                "m",
                "--scanner-mode",
                "fake-clean",
            ]
        )
        == 2
    )
    assert called is False


def test_real_image_gate_cli_rejects_dotdot_alias_before_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alias = (
        gate.CANONICAL_IMAGE_PATH.parent
        / ".."
        / gate.CANONICAL_IMAGE_PATH.parent.name
        / gate.CANONICAL_IMAGE_PATH.name
    )
    monkeypatch.setattr(
        gate, "_provider_environment", lambda *a, **k: pytest.fail("provider constructed")
    )
    with pytest.raises(gate.GateBlocked):
        gate._resolve_image(alias)


def test_real_image_gate_cli_rejects_symlink_path_before_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    link = tmp_path / "image.png"
    link.symlink_to(gate.CANONICAL_IMAGE_PATH)
    monkeypatch.setattr(
        gate, "_provider_environment", lambda *a, **k: pytest.fail("provider constructed")
    )
    with pytest.raises(gate.GateBlocked):
        gate._resolve_image(link)


def test_canonical_image_path_is_regular_and_not_symlink() -> None:
    assert gate.CANONICAL_IMAGE_PATH.is_file()
    assert not gate.CANONICAL_IMAGE_PATH.is_symlink()


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("CANONICAL_IMAGE_SHA256", "0" * 64),
        ("CANONICAL_IMAGE_SIZE", 1),
        ("CANONICAL_PNG_SIGNATURE", b"not-png!"),
    ],
)
def test_canonical_image_identity_is_verified_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch, attribute: str, value: object
) -> None:
    monkeypatch.setattr(gate, attribute, value)
    with pytest.raises(gate.GateBlocked):
        gate._resolve_image(gate.CANONICAL_IMAGE_PATH)


def test_real_image_gate_cli_passes_locked_runtime_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        gate,
        "load_project_env",
        lambda root: {
            "DASHSCOPE_" + "API_KEY": "safe-test-key",
            "DASHSCOPE_BASE_URL": "https://example.invalid",
        },
    )
    configured, qualified = gate._provider_environment("fake-clean", "openai", "model")
    assert qualified == "openai/model"
    assert configured["FOCUSPROOF_PROFILE"] == "demo-real-vision"
    assert configured["FOCUSPROOF_LLM_NUM_RETRIES"] == "0"
    assert configured["FOCUSPROOF_LLM_MAX_CONCURRENT_REVIEWS"] == "1"


def test_real_image_gate_restores_environment_and_never_uses_generator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOCUSPROOF_LLM_MODEL", "before")
    with gate._temporary_environment({"FOCUSPROOF_LLM_MODEL": "during", "VISUAL_GATE_TEMP": "x"}):
        assert os.environ["FOCUSPROOF_LLM_MODEL"] == "during"
    assert os.environ["FOCUSPROOF_LLM_MODEL"] == "before"
    assert "VISUAL_GATE_TEMP" not in os.environ
    assert "imagegen" not in inspect.getsource(gate).lower()


def test_cli_configuration_reaches_official_sdk_llm_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gate,
        "load_project_env",
        lambda root: {"OPENAI_API_KEY": "safe", "OPENAI_BASE_URL": "https://example.invalid"},
    )
    configured, _ = gate._provider_environment("fake-clean", "openai", "model")
    assert configured["FOCUSPROOF_LLM_PROVIDER"] == "openai"
    assert configured["FOCUSPROOF_LLM_SUPPORTS_VISION"] == "true"


def test_environment_is_restored_when_product_chain_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESTORE_ME", "before")
    with pytest.raises(RuntimeError):
        with gate._temporary_environment({"RESTORE_ME": "during"}):
            raise RuntimeError("boom")
    assert os.environ["RESTORE_ME"] == "before"


def test_runner_has_no_image_generation_code_and_passes_canonical_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = inspect.getsource(gate)
    assert "image_generation" not in source
    assert gate._resolve_image(gate.CANONICAL_IMAGE_PATH) == gate.CANONICAL_IMAGE_PATH


def _safe_failure(reason: str = "blocked") -> dict[str, object]:
    return {"status": "FAIL", "reason": reason, "assurance": gate._assurance("fake-clean")}


def _safe_pass() -> dict[str, object]:
    return {
        "status": "PASS",
        "provider": "openai",
        "model": "model",
        "scanner_mode": "fake-clean",
        "sdk": {"name": "openhands-sdk", "version": "1.31.0"},
        "image": {"path": "/canonical/image.png", "size": 1, "sha256": "a" * 64},
        "conversationId": "conversation_1",
        "eventTypes": ["ObservationEvent"],
        "nativeEvents": {"actionCount": 1, "observationCount": 1},
        "checks": {
            "imagePayloadNotPersisted": True,
            "officialConversationUsed": True,
            "realRuntimeUsed": True,
            "visionActive": True,
            "productionLlmUsed": True,
            "nativeActionObserved": True,
            "nativeObservationObserved": True,
            "mediaToolUsed": True,
            "monadDisabled": True,
        },
        "limits": {"maxCallsPerReview": 6, "maxReviewSeconds": 120, "maxConcurrentReviews": 1},
        "assurance": gate._assurance("fake-clean"),
    }


def _native_media_events(
    *,
    status: str = "success",
    visual_facts: list[str] | None = None,
    error_code: str | None = None,
) -> tuple[ActionEvent, ObservationEvent]:
    now = datetime.now(UTC)
    tool_call = MessageToolCall(
        id="call_media_contract",
        name=gate._MEDIA_VERIFICATION_TOOL_NAME,
        arguments='{"evidence_id":"ev_image"}',
        origin="completion",
    )
    action = ActionEvent(
        thought=[TextContent(text="Verify the uploaded image evidence")],
        action=EvidenceReferenceAction(evidence_id="ev_image"),
        tool_name=tool_call.name,
        tool_call_id=tool_call.id,
        tool_call=tool_call,
        llm_response_id="response_media_contract",
    )
    observation = ObservationEvent(
        tool_name=tool_call.name,
        tool_call_id=tool_call.id,
        observation=VerificationObservation.from_text(
            "verified media facts",
            evidence_id="ev_image",
            capability="image",
            status=status,
            facts={
                "visual_facts": visual_facts
                or [
                    "A topic title is visible near the top.",
                    "A goal statement appears beneath the topic.",
                    "An image evidence card is shown in the composer.",
                ],
            },
            weak_signals=[],
            source_refs=["ev_image"],
            verifier_version="1",
            started_at=now,
            completed_at=now,
            error_code=error_code,
        ),
        action_id=action.id,
    )
    return action, observation


def test_report_schema_rejects_unlocked_runtime_metadata(tmp_path: Path) -> None:
    payload = _safe_failure()
    payload["runtime"] = {"retries": 9}
    with pytest.raises(gate.GateFailed):
        gate._write_report(tmp_path / "r.json", payload)


def test_report_schema_accepts_clamd_assurance_without_scan_certification(
    tmp_path: Path,
) -> None:
    payload = _safe_failure()
    payload["assurance"] = gate._assurance("clamd")
    gate._write_report(tmp_path / "r.json", payload)
    assert json.loads((tmp_path / "r.json").read_text())["assurance"]["mediaScanner"] == "clamd"


def test_report_schema_accepts_safe_metadata_and_assurance(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    gate._write_report(path, _safe_failure())
    assert json.loads(path.read_text())["gate"] == "real_visual_provider"


def test_failure_report_accepts_safe_diagnostics_and_writes_sidecar_hash(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    gate._publish_report_pair(
        report, _safe_failure(), diagnostics=gate._empty_failure_diagnostics()
    )
    sidecar = json.loads(report.with_suffix(".json.sha256").read_text())
    assert sidecar["reportSha256"] == hashlib.sha256(report.read_bytes()).hexdigest()


def test_failure_report_persists_response_completion_and_visual_fact_diagnostics(
    tmp_path: Path,
) -> None:
    d = gate._empty_failure_diagnostics()
    d.update({"responseReceived": True, "completionSucceeded": True, "visualFactsCount": 3})
    report = tmp_path / "r.json"
    gate._publish_report_pair(report, _safe_failure(), diagnostics=d)
    assert json.loads(report.read_text())["diagnostics"]["visualFactsCount"] == 3


def test_failure_report_accepts_clamd_assurance_without_scan_certification(
    tmp_path: Path,
) -> None:
    report = tmp_path / "r.json"
    payload = _safe_failure()
    payload["assurance"] = gate._assurance("clamd")
    gate._publish_report_pair(report, payload, diagnostics=gate._empty_failure_diagnostics())
    assert json.loads(report.read_text())["assurance"]["mediaScanner"] == "clamd"


def test_report_includes_runner_source_sha256(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    gate._publish_report_pair(
        report, _safe_failure(), diagnostics=gate._empty_failure_diagnostics()
    )
    assert len(json.loads(report.read_text())["runnerSourceSha256"]) == 64


def test_failure_report_rejects_diagnostic_leaks_and_oversize(tmp_path: Path) -> None:
    with pytest.raises(gate.GateFailed):
        gate._publish_report_pair(tmp_path / "r.json", _safe_failure(), diagnostics={"raw": "x"})
    with pytest.raises(gate.GateFailed):
        gate._publish_report_pair(
            tmp_path / "r2.json", _safe_failure(), diagnostics={"reason": "x" * 9000}
        )


def test_atomic_write_preserves_existing_report_when_replace_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "r"
    path.write_text("old")
    monkeypatch.setattr(
        Path, "replace", lambda self, target: (_ for _ in ()).throw(OSError("replace"))
    )
    with pytest.raises(OSError):
        gate._atomic_write_text(path, "new")
    assert path.read_text() == "old"


def test_failure_report_publish_restores_previous_pair_when_sidecar_replace_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "r.json"
    sidecar = report.with_suffix(".json.sha256")
    report.write_text("old-report")
    sidecar.write_text("old-sidecar")
    monkeypatch.setattr(gate, "_atomic_write_text", gate._atomic_write_text)
    with pytest.raises(gate.GateFailed):
        gate._publish_report_pair(
            report, _safe_failure(), diagnostics={"forceSidecarFailure": True}
        )
    assert (report.read_text(), sidecar.read_text()) == ("old-report", "old-sidecar")


def test_pass_report_publish_restores_previous_pair_when_sidecar_replace_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "p.json"
    sidecar = report.with_suffix(".json.sha256")
    report.write_text("old-pass")
    sidecar.write_text("old-pass-sidecar")
    with pytest.raises(gate.GateFailed):
        gate._publish_report_pair(report, _safe_pass(), diagnostics={"forceSidecarFailure": True})
    assert (report.read_text(), sidecar.read_text()) == ("old-pass", "old-pass-sidecar")


def test_failure_report_publish_leaves_no_new_pair_when_report_replace_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        Path, "replace", lambda self, target: (_ for _ in ()).throw(OSError("replace"))
    )
    with pytest.raises(OSError):
        gate._publish_report_pair(tmp_path / "r.json", _safe_failure(), diagnostics={})
    assert not (tmp_path / "r.json").exists()


def test_pass_report_publish_leaves_no_new_pair_when_report_replace_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        Path, "replace", lambda self, target: (_ for _ in ()).throw(OSError("replace"))
    )
    with pytest.raises(OSError):
        gate._publish_report_pair(tmp_path / "p.json", _safe_pass(), diagnostics={})
    assert not (tmp_path / "p.json").exists()


def test_main_writes_default_failure_diagnostics_when_blocked_before_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "r.json"
    monkeypatch.setattr(
        gate, "_resolve_image", lambda p: (_ for _ in ()).throw(gate.GateBlocked("blocked"))
    )
    assert (
        gate.main(
            [
                "--execute-real-provider",
                "--report",
                str(report),
                "--image",
                "x",
                "--provider",
                "openai",
                "--model",
                "m",
                "--scanner-mode",
                "fake-clean",
            ]
        )
        == 2
    )
    assert json.loads(report.read_text())["diagnostics"]["providerAttempted"] is False


def test_main_persists_structured_failure_diagnostics_from_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "r.json"
    monkeypatch.setattr(gate, "_resolve_image", lambda p: gate.CANONICAL_IMAGE_PATH)
    monkeypatch.setattr(
        gate, "_provider_environment", lambda *a: ({"FOCUSPROOF_LLM_API_KEY": "safe"}, "openai/m")
    )
    monkeypatch.setattr(
        gate,
        "_run_product_chain",
        lambda *a: (_ for _ in ()).throw(
            gate.GateDiagnosticFailure("failed", {"providerAttempted": True})
        ),
    )
    assert (
        gate.main(
            [
                "--execute-real-provider",
                "--report",
                str(report),
                "--image",
                str(gate.CANONICAL_IMAGE_PATH),
                "--provider",
                "openai",
                "--model",
                "m",
                "--scanner-mode",
                "fake-clean",
            ]
        )
        == 1
    )
    assert json.loads(report.read_text())["diagnostics"]["providerAttempted"] is True


@pytest.mark.parametrize(
    "payload", [{"unknown": "field"}, {"raw": "A" * 40}, {"image_content": "x"}, {"token": "x"}]
)
def test_report_schema_rejects_unknown_or_inline_payloads(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_safe_payload(payload)


@pytest.mark.parametrize(
    "binary_value", [b"raw-image", bytearray(b"raw-image"), memoryview(b"raw-image")]
)
def test_report_schema_rejects_binary_and_known_secret(
    tmp_path: Path, binary_value: object
) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_safe_payload({"reason": binary_value})


@pytest.mark.parametrize("inline_value", ["data:image/png;base64,QUFB", "A" * 9000, "B" * 9000])
def test_report_schema_rejects_data_url_and_large_inline_strings(
    tmp_path: Path, inline_value: str
) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_safe_payload({"reason": inline_value})


@pytest.mark.parametrize(
    "inline_value",
    [
        "QUJDREVGR0hJSktMTQ==",
        "iVBORw0KGgo=",
        "raw-image pixels attached",
        "raw pixels copied from framebuffer",
    ],
)
def test_report_schema_rejects_short_base64_image_magic_and_raw_markers(
    tmp_path: Path, inline_value: str
) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_safe_payload({"reason": inline_value})


def test_report_schema_rejects_string_length_limit_before_write(tmp_path: Path) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_safe_payload({"reason": "x" * 8193})


def test_report_schema_rejects_list_length_limit_before_write(tmp_path: Path) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_safe_payload({"events": [1] * 257})


def test_report_schema_rejects_total_json_size_limit_before_write(tmp_path: Path) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_safe_payload({"events": ["x" * 1000] * 100})


def test_report_schema_rejects_recursive_depth_limit() -> None:
    value: dict[str, object] = {}
    cursor = value
    for _ in range(20):
        cursor["nested"] = {}
        cursor = cursor["nested"]  # type: ignore[assignment]
    with pytest.raises(gate.GateFailed):
        gate._validate_safe_payload(value)


def test_main_rejects_unselected_provider_secret_before_persisting_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_safe_payload({"reason": "known-secret"}, known_secrets={"known-secret"})


def test_production_llm_detection_uses_official_testllm_isinstance() -> None:
    from openhands.sdk.testing import TestLLM

    assert gate._production_llm_used(TestLLM(model="test-model")) is False
    assert gate._production_llm_used.__module__ == gate.__name__


def test_fail_report_persists_redacted_diagnostics_eventlog_summary_and_sha_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "r.json"
    gate._publish_report_pair(
        report, _safe_failure("safe"), diagnostics={"transportOutcome": "failed"}
    )
    assert report.exists() and report.with_suffix(".json.sha256").exists()


def test_plain_gatefailed_without_native_events_writes_requested_failure_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = gate._safe_eventlog_summary([], gate._empty_failure_diagnostics())
    assert summary["eventCounts"] == {"messageCount": 0, "actionCount": 0, "observationCount": 0}


def test_audit_summary_never_persists_visual_fact_text_or_ocr_pii(tmp_path: Path) -> None:
    with pytest.raises(gate.GateFailed):
        gate._safe_eventlog_entry({"factText": "name@example.com"})


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_score_event",
        "missing_projection",
        "extra_projection",
        "duplicate_fact_id",
        "conflicting_digest",
        "invalid_digest",
        "missing_source_observation",
    ],
)
def test_pass_summary_rejects_ambiguous_or_broken_audit_lineage(
    mutation: str, tmp_path: Path
) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_v3_summary(gate._v3_fixture(mutation=mutation))


def test_legacy_only_lineage_cannot_be_written_as_v3_explicit(tmp_path: Path) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_v3_summary({"schemaVersion": "2.0"})


@pytest.mark.parametrize(
    "target",
    ["projection", "source", "fact", "score", "review", "review_score", "review_projection"],
)
@pytest.mark.parametrize("bad_id", [7, None, "", " leading", "trailing "])
def test_v3_lineage_rejects_non_strict_ids(target: str, bad_id: object) -> None:
    fixture = gate._v3_fixture()
    fixture[target] = bad_id
    with pytest.raises(gate.GateFailed):
        gate._validate_v3_summary(fixture)


@pytest.mark.parametrize("mutation", ["missing", "extra", "different", "duplicate"])
def test_v3_lineage_rejects_invalid_scoring_consumed_sets(mutation: str) -> None:
    with pytest.raises(gate.GateFailed):
        gate._validate_v3_summary(gate._v3_fixture(consumed_mutation=mutation))


def test_v3_summary_validator_rejects_count_mismatch() -> None:
    fixture = gate._v3_fixture()
    fixture["consumedFactCount"] = 2
    with pytest.raises(gate.GateFailed):
        gate._validate_v3_summary(fixture)


def test_checked_in_v3_fixture_passes_shared_validator_and_hash() -> None:
    fixture = gate._v3_fixture()
    gate._validate_v3_summary(fixture)
    assert (
        hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest()
        == gate._v3_fixture_sha256()
    )


def test_pass_and_fail_summary_never_persist_sensitive_report_path(tmp_path: Path) -> None:
    summary = gate._safe_eventlog_summary(
        [], gate._empty_failure_diagnostics(), report_path=tmp_path / "secret-name.json"
    )
    assert "secret-name" not in json.dumps(summary)


def test_pass_path_writes_requested_auditable_eventlog_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = gate._safe_eventlog_summary(
        [{"eventType": "ObservationEvent", "id": "obs_1"}], {"visualFactsCount": 1}
    )
    assert summary["eventCounts"]["observationCount"] == 1


def test_pass_path_does_not_write_eventlog_summary_when_not_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "r.json"
    gate._publish_report_pair(report, _safe_failure(), diagnostics={})
    assert not (tmp_path / "eventlog-summary.json").exists()


def test_eventlog_summary_caps_size_without_leaking_message_or_visual_payload(
    tmp_path: Path,
) -> None:
    events = [
        {"eventType": "MessageEvent", "id": f"m_{i}", "message": "secret"} for i in range(1000)
    ]
    rendered = json.dumps(gate._safe_eventlog_summary(events, {}))
    assert len(rendered) < 65536 and "secret" not in rendered


@pytest.mark.parametrize(
    ("diagnostics", "expected"),
    [
        ({"providerAttempted": False}, "unknown"),
        ({"providerAttempted": True, "responseReceived": False}, "failed"),
        (
            {"providerAttempted": True, "responseReceived": True, "completionSucceeded": False},
            "invalid",
        ),
        (
            {"providerAttempted": True, "responseReceived": True, "completionSucceeded": True},
            "received",
        ),
    ],
)
def test_transport_outcome_requires_evidence_before_marking_received(
    diagnostics: dict[str, object], expected: str
) -> None:
    assert gate._infer_transport_outcome(diagnostics) == expected


def test_official_llm_observer_enters_and_restores_completion_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = SimpleNamespace(completion=lambda *a, **k: {"choices": [{"message": {"content": "ok"}}]})
    original = llm.completion
    with gate._observe_completion_boundary(llm) as state:
        assert llm.completion()["choices"] and state["attempts"] == 1
    assert llm.completion is original


def test_official_llm_observer_restores_on_exception_and_ignores_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = SimpleNamespace(completion=lambda: (_ for _ in ()).throw(RuntimeError("transport")))
    peer = SimpleNamespace(completion=lambda: "peer")
    with pytest.raises(RuntimeError):
        with gate._observe_completion_boundary(llm) as state:
            llm.completion()
    assert state["attempts"] == 1 and peer.completion() == "peer"


def test_completion_observer_marks_attempt_without_response_on_transport_exception() -> None:
    state = gate._observe_completion(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert state == {"attempts": 1, "responseReceived": False, "completionSucceeded": False}


def test_completion_observer_separates_missing_response_from_invalid_response() -> None:
    assert gate._observe_completion(lambda: None)["responseReceived"] is False
    assert gate._observe_completion(lambda: {})["completionSucceeded"] is False


def test_completion_observer_does_not_leak_success_state_across_runs() -> None:
    assert gate._observe_completion(lambda: {"choices": [1]})["completionSucceeded"] is True
    assert gate._observe_completion(lambda: None)["completionSucceeded"] is False


def test_completion_observer_resets_last_attempt_state_within_single_scope() -> None:
    observer = gate.CompletionObserver()
    observer.observe(lambda: {"choices": [1]})
    observer.observe(lambda: None)
    assert observer.last["responseReceived"] is False and observer.total_attempts == 2


def test_completion_observer_resets_success_then_transport_within_single_scope() -> None:
    observer = gate.CompletionObserver()
    observer.observe(lambda: {"choices": [1]})
    with pytest.raises(RuntimeError):
        observer.observe(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert observer.last["completionSucceeded"] is False


def test_runtime_diagnostics_reads_real_native_media_events() -> None:
    action, observation = _native_media_events()
    diagnostics = gate._runtime_diagnostics(
        [action, observation], provider="openai", model="qwen3.7-plus"
    )

    assert diagnostics["officialEventCounts"] == {
        "messageCount": 0,
        "actionCount": 1,
        "observationCount": 1,
    }
    assert diagnostics["visualProvider"] == {"provider": "openai", "model": "qwen3.7-plus"}
    assert diagnostics["mediaObservation"] == {
        "toolName": gate._MEDIA_VERIFICATION_TOOL_NAME,
        "observationClass": "VerificationObservation",
        "status": "success",
        "capability": "image",
        "visualFactsCount": 3,
        "errorCode": "",
    }
    assert diagnostics["nativeEventSummary"] == [
        {
            "eventType": "ActionEvent",
            "id": str(action.id),
            "toolName": gate._MEDIA_VERIFICATION_TOOL_NAME,
            "actionClass": "EvidenceReferenceAction",
        },
        {
            "eventType": "ObservationEvent",
            "id": str(observation.id),
            "toolName": gate._MEDIA_VERIFICATION_TOOL_NAME,
            "observationClass": "VerificationObservation",
            "status": "success",
            "capability": "image",
            "visualFactsCount": 3,
            "errorCode": "",
        },
    ]


def test_safe_eventlog_summary_preserves_only_safe_fields_from_real_native_events() -> None:
    action, observation = _native_media_events()
    diagnostics = gate._runtime_diagnostics([action, observation])
    summary = gate._safe_eventlog_summary([action, observation], diagnostics)
    summary["schemaVersion"] = "3.0"

    gate._validate_safe_eventlog_summary(summary)

    assert summary["events"] == diagnostics["nativeEventSummary"]
    assert "verified media facts" not in json.dumps(summary)
    assert "visual_facts" not in json.dumps(summary)


def test_run_product_chain_rejects_completed_review_without_three_visual_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(gate.GateFailed):
        gate._require_product_success(
            {"reviewStatus": "completed", "visualFactsCount": 2, "completionSucceeded": True}
        )


def test_run_product_chain_marks_review_503_without_completion_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = gate._failure_diagnostics(review_status="http-503")
    assert d["providerAttempted"] is False and d["reviewStatus"] == "http-503"


def test_run_product_chain_marks_transport_exception_at_completion_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = gate._failure_diagnostics(provider_attempted=True, transport_outcome="failed")
    assert d["transportOutcome"] == "failed"


def test_product_failure_diagnostics_preserve_provider_attempts_for_review_503() -> None:
    action, observation = _native_media_events()

    diagnostics = gate._product_failure_diagnostics(
        SimpleNamespace(
            conversation=SimpleNamespace(
                state=SimpleNamespace(events=[action, observation]),
            )
        ),
        {"attempts": 1, "responseReceived": True, "completionSucceeded": False},
        SimpleNamespace(total_attempts=1),
        provider="openai",
        model="model",
        review_status="http-503",
    )

    assert diagnostics["providerAttempted"] is True
    assert diagnostics["responseReceived"] is True
    assert diagnostics["transportOutcome"] == "invalid"
    assert diagnostics["visualFactsCount"] == 3
    assert diagnostics["visualFactsParsed"] is True
    assert diagnostics["reviewStatus"] == "http-503"
    assert diagnostics["totalProviderCompletionCalls"] == 1
    assert diagnostics["mediaObservation"] == {
        "toolName": gate._MEDIA_VERIFICATION_TOOL_NAME,
        "observationClass": "VerificationObservation",
        "status": "success",
        "capability": "image",
        "visualFactsCount": 3,
        "errorCode": "",
    }


def test_run_product_chain_only_passes_with_full_completion_and_three_visual_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostics: dict[str, object] = {
        "attempts": 1,
        "responseReceived": True,
        "completionSucceeded": True,
        "providerAttempted": True,
        "transportOutcome": "received",
        "visualFactsCount": 3,
        "reviewStatus": "completed",
        "visualFactsParsed": True,
        "visualProvider": {"provider": "openai", "model": "model"},
    }
    summary = gate._v3_fixture()
    events: list[dict[str, object]] = []
    summary.update(
        {
            "eventCounts": {"messageCount": 0, "actionCount": 0, "observationCount": 0},
            "events": events,
            "diagnostics": diagnostics,
            "digest": hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest(),
        }
    )
    gate._require_product_success(
        {
            "reviewStatus": "completed",
            "diagnostics": diagnostics,
            "v3Summary": summary,
            "eventlogSummary": summary,
            "checks": {"productionLlmUsed": False},
            "expectedProvider": "openai",
            "expectedModel": "model",
            "expectedProductionLlmUsed": False,
        }
    )


def test_completion_observer_counts_all_provider_completions() -> None:
    observer = gate.CompletionObserver()
    observer.observe(lambda: {"choices": [1]})
    observer.observe(lambda: {"choices": [2]})
    assert observer.total_attempts == 2


def test_runtime_diagnostics_extracts_visual_provider_attribution() -> None:
    d = gate._runtime_diagnostics([], provider="openai", model="qwen3.7-plus")
    assert d["visualProvider"] == {"provider": "openai", "model": "qwen3.7-plus"}


def test_agent_decision_completion_count_is_null_when_visual_count_is_unreliable() -> None:
    assert gate._agent_decision_count(total=2, visual=None) is None
    assert gate._agent_decision_count(total=3, visual=1) == 2
