from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


class BundleFailed(RuntimeError):
    pass


_VISUAL_TOOL_NAME = "focusproof_media_evidence_verification"
_EXPECTED_CLAMD_CASES: dict[str, tuple[str, bool, str | None]] = {
    "benign_png": ("clean", True, None),
    "eicar": ("malicious", False, "malware_signature_detected"),
    "timeout": ("timeout", False, "deadline_exceeded"),
    "unavailable": ("unavailable", False, "daemon_unavailable"),
    "error": ("error", False, "daemon_error"),
}


def _read_json_dict(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BundleFailed(f"required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BundleFailed(f"invalid json: {path}") from exc
    if not isinstance(value, dict):
        raise BundleFailed(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expect_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BundleFailed(f"{label} must be an object")
    return value


def _expect_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BundleFailed(f"{label} must be a non-empty string")
    return value


def _expect_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise BundleFailed(f"{label} must be a bool")
    return value


def _expect_int(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise BundleFailed(f"{label} must be an int >= {minimum}")
    return value


def _report_hash_record(path: Path) -> dict[str, object]:
    digest = _sha256(path)
    sidecar_path = path.with_suffix(path.suffix + ".sha256")
    sidecar_verified = False
    if sidecar_path.exists():
        sidecar = _read_json_dict(sidecar_path)
        if sidecar.get("schemaVersion") != "1.0":
            raise BundleFailed(f"invalid sidecar schema: {sidecar_path}")
        sidecar_digest = _expect_str(sidecar.get("reportSha256"), f"{sidecar_path} reportSha256")
        if sidecar_digest != digest:
            raise BundleFailed(f"sidecar sha256 mismatch: {path}")
        sidecar_verified = True
    return {
        "path": str(path),
        "sha256": digest,
        "sidecarPath": str(sidecar_path) if sidecar_path.exists() else "",
        "sidecarVerified": sidecar_verified,
    }


def _validate_visual_report(report: dict[str, object]) -> dict[str, object]:
    if report.get("status") != "PASS":
        raise BundleFailed("visual report status must be PASS")
    assurance = _expect_dict(report.get("assurance"), "visual assurance")
    if assurance.get("productionMalwareScanningVerified") is not False:
        raise BundleFailed("visual report must not certify production malware scanning")
    checks = _expect_dict(report.get("checks"), "visual checks")
    diagnostics = _expect_dict(report.get("diagnostics"), "visual diagnostics")
    media = _expect_dict(diagnostics.get("mediaObservation"), "visual mediaObservation")
    eventlog = _expect_dict(report.get("eventlogSummary"), "visual eventlogSummary")
    native_counts = _expect_dict(report.get("nativeEvents"), "visual nativeEvents")

    required_true = (
        ("providerAttempted", diagnostics.get("providerAttempted")),
        ("responseReceived", diagnostics.get("responseReceived")),
        ("completionSucceeded", diagnostics.get("completionSucceeded")),
        ("visualFactsParsed", diagnostics.get("visualFactsParsed")),
        ("officialConversationUsed", checks.get("officialConversationUsed")),
        ("realRuntimeUsed", checks.get("realRuntimeUsed")),
        ("productionLlmUsed", checks.get("productionLlmUsed")),
        ("nativeActionObserved", checks.get("nativeActionObserved")),
        ("nativeObservationObserved", checks.get("nativeObservationObserved")),
        ("mediaToolUsed", checks.get("mediaToolUsed")),
        ("visionActive", checks.get("visionActive")),
        ("monadDisabled", checks.get("monadDisabled")),
        ("imagePayloadNotPersisted", checks.get("imagePayloadNotPersisted")),
    )
    for label, value in required_true:
        if _expect_bool(value, f"visual {label}") is not True:
            raise BundleFailed(f"visual {label} must be true")

    if _expect_str(report.get("provider"), "visual provider") != "openai":
        raise BundleFailed("visual provider must be openai")
    model = _expect_str(report.get("model"), "visual model")
    if _expect_str(diagnostics.get("reviewStatus"), "visual reviewStatus") != "completed":
        raise BundleFailed("visual reviewStatus must be completed")
    visual_facts_count = _expect_int(
        diagnostics.get("visualFactsCount"),
        "visual visualFactsCount",
        minimum=3,
    )
    if _expect_str(media.get("toolName"), "visual media toolName") != _VISUAL_TOOL_NAME:
        raise BundleFailed("visual mediaObservation toolName is invalid")
    if (
        _expect_str(media.get("observationClass"), "visual observationClass")
        != "VerificationObservation"
    ):
        raise BundleFailed("visual mediaObservation class is invalid")
    if _expect_str(media.get("status"), "visual media status") != "success":
        raise BundleFailed("visual mediaObservation status must be success")
    if _expect_str(media.get("capability"), "visual media capability") != "image":
        raise BundleFailed("visual mediaObservation capability must be image")
    if (
        _expect_int(
            media.get("visualFactsCount"), "visual mediaObservation visualFactsCount", minimum=3
        )
        < 3
    ):
        raise BundleFailed("visual mediaObservation facts must be >= 3")
    if _expect_str(diagnostics.get("transportOutcome"), "visual transportOutcome") != "received":
        raise BundleFailed("visual transportOutcome must be received")
    if _expect_int(native_counts.get("actionCount"), "visual native actionCount", minimum=1) < 1:
        raise BundleFailed("visual native actionCount must be >= 1")
    if (
        _expect_int(
            native_counts.get("observationCount"), "visual native observationCount", minimum=1
        )
        < 1
    ):
        raise BundleFailed("visual native observationCount must be >= 1")
    if _expect_int(eventlog.get("consumedFactCount"), "visual consumedFactCount", minimum=3) < 3:
        raise BundleFailed("visual consumedFactCount must be >= 3")

    return {
        "provider": "openai",
        "model": model,
        "reviewStatus": "completed",
        "providerAttempted": True,
        "responseReceived": True,
        "completionSucceeded": True,
        "visualFactsCount": visual_facts_count,
        "mediaObservation": {
            "toolName": _VISUAL_TOOL_NAME,
            "observationClass": "VerificationObservation",
            "status": "success",
            "capability": "image",
            "visualFactsCount": _expect_int(
                media.get("visualFactsCount"),
                "visual mediaObservation visualFactsCount",
                minimum=3,
            ),
        },
        "nativeEvents": {
            "actionCount": _expect_int(
                native_counts.get("actionCount"), "visual native actionCount", minimum=1
            ),
            "observationCount": _expect_int(
                native_counts.get("observationCount"),
                "visual native observationCount",
                minimum=1,
            ),
        },
    }


def _validate_live_clamd_report(report: dict[str, object]) -> dict[str, object]:
    if report.get("status") != "PASS":
        raise BundleFailed("live clamd report status must be PASS")
    if _expect_bool(report.get("liveClamdExecuted"), "liveClamdExecuted") is not True:
        raise BundleFailed("liveClamdExecuted must be true")
    if (
        _expect_bool(
            report.get("productionMalwareScanningVerified"), "productionMalwareScanningVerified"
        )
        is not True
    ):
        raise BundleFailed("productionMalwareScanningVerified must be true")
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise BundleFailed("live clamd cases must be a list")
    actual_cases: dict[str, dict[str, object]] = {}
    for item in cases:
        case = _expect_dict(item, "live clamd case")
        name = _expect_str(case.get("name"), "live clamd case name")
        actual_cases[name] = case
    if set(actual_cases) != set(_EXPECTED_CLAMD_CASES):
        raise BundleFailed("live clamd cases are incomplete")

    rendered_cases: list[dict[str, object]] = []
    for name in sorted(_EXPECTED_CLAMD_CASES):
        outcome, finalized, rejection_code = _EXPECTED_CLAMD_CASES[name]
        case = actual_cases[name]
        if _expect_str(case.get("outcome"), f"{name} outcome") != outcome:
            raise BundleFailed(f"{name} outcome is invalid")
        if _expect_bool(case.get("passed"), f"{name} passed") is not True:
            raise BundleFailed(f"{name} must pass")
        if _expect_bool(case.get("finalized"), f"{name} finalized") is not finalized:
            raise BundleFailed(f"{name} finalized flag is invalid")
        actual_rejection = case.get("rejectionCode")
        if rejection_code is None:
            if actual_rejection is not None:
                raise BundleFailed(f"{name} rejectionCode must be null")
        elif _expect_str(actual_rejection, f"{name} rejectionCode") != rejection_code:
            raise BundleFailed(f"{name} rejectionCode is invalid")
        rendered_cases.append(
            {
                "name": name,
                "outcome": outcome,
                "finalized": finalized,
                "rejectionCode": rejection_code,
            }
        )
    return {
        "liveClamdExecuted": True,
        "productionMalwareScanningVerified": True,
        "cases": rendered_cases,
    }


def build_manifest(visual_report: Path, live_clamd_report: Path) -> dict[str, object]:
    visual = _validate_visual_report(_read_json_dict(visual_report))
    live = _validate_live_clamd_report(_read_json_dict(live_clamd_report))
    return {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(UTC).isoformat(),
        "bundle": "final_dual_mode_acceptance",
        "status": "PASS",
        "visualReport": {**_report_hash_record(visual_report), **visual},
        "liveClamdReport": {**_report_hash_record(live_clamd_report), **live},
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sidecar = {
        "schemaVersion": "1.0",
        "reportSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    path.with_suffix(path.suffix + ".sha256").write_text(
        json.dumps(sidecar, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--visual-report", type=Path, required=True)
    parser.add_argument("--live-clamd-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        manifest = build_manifest(args.visual_report, args.live_clamd_report)
    except BundleFailed as exc:
        _write_json(
            args.output,
            {
                "schemaVersion": "1.0",
                "generatedAt": datetime.now(UTC).isoformat(),
                "bundle": "final_dual_mode_acceptance",
                "status": "FAIL",
                "reason": str(exc),
                "visualReportPath": str(args.visual_report),
                "liveClamdReportPath": str(args.live_clamd_report),
            },
        )
        return 1
    _write_json(args.output, manifest)
    print(json.dumps({"status": manifest["status"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
