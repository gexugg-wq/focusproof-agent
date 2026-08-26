from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import build_final_dual_mode_manifest as bundle


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _visual_report() -> dict[str, object]:
    return {
        "status": "PASS",
        "provider": "openai",
        "model": "qwen3.7-plus",
        "assurance": {
            "mediaScanner": "clamd",
            "productionMalwareScanningVerified": False,
            "scope": "local-test-only",
        },
        "checks": {
            "imagePayloadNotPersisted": True,
            "mediaToolUsed": True,
            "pluginCapabilitiesAbsent": True,
            "nativeActionObserved": True,
            "nativeObservationObserved": True,
            "officialConversationUsed": True,
            "productionLlmUsed": True,
            "realRuntimeUsed": True,
            "visionActive": True,
        },
        "diagnostics": {
            "providerAttempted": True,
            "responseReceived": True,
            "completionSucceeded": True,
            "reviewStatus": "completed",
            "transportOutcome": "received",
            "visualFactsParsed": True,
            "visualFactsCount": 4,
            "mediaObservation": {
                "toolName": "focusproof_media_evidence_verification",
                "observationClass": "VerificationObservation",
                "status": "success",
                "capability": "image",
                "visualFactsCount": 4,
                "errorCode": "",
            },
        },
        "eventlogSummary": {"consumedFactCount": 4},
        "nativeEvents": {"actionCount": 2, "observationCount": 2},
    }


def _live_report() -> dict[str, object]:
    return {
        "status": "PASS",
        "liveClamdExecuted": True,
        "productionMalwareScanningVerified": True,
        "cases": [
            {
                "name": "benign_png",
                "outcome": "clean",
                "passed": True,
                "finalized": True,
                "rejectionCode": None,
            },
            {
                "name": "eicar",
                "outcome": "malicious",
                "passed": True,
                "finalized": False,
                "rejectionCode": "malware_signature_detected",
            },
            {
                "name": "timeout",
                "outcome": "timeout",
                "passed": True,
                "finalized": False,
                "rejectionCode": "deadline_exceeded",
            },
            {
                "name": "unavailable",
                "outcome": "unavailable",
                "passed": True,
                "finalized": False,
                "rejectionCode": "daemon_unavailable",
            },
            {
                "name": "error",
                "outcome": "error",
                "passed": True,
                "finalized": False,
                "rejectionCode": "daemon_error",
            },
        ],
    }


def test_manifest_builder_passes_and_verifies_visual_sidecar(tmp_path: Path) -> None:
    visual_report = tmp_path / "real.json"
    live_report = tmp_path / "live.json"
    output = tmp_path / "bundle.json"
    _write_json(visual_report, _visual_report())
    _write_json(live_report, _live_report())
    visual_digest = hashlib.sha256(visual_report.read_bytes()).hexdigest()
    visual_report.with_suffix(".json.sha256").write_text(
        json.dumps({"schemaVersion": "1.0", "reportSha256": visual_digest}) + "\n",
        encoding="utf-8",
    )

    assert (
        bundle.main(
            [
                "--visual-report",
                str(visual_report),
                "--live-clamd-report",
                str(live_report),
                "--output",
                str(output),
            ]
        )
        == 0
    )

    manifest = json.loads(output.read_text())
    sidecar = json.loads(output.with_suffix(".json.sha256").read_text())
    assert manifest["status"] == "PASS"
    assert manifest["visualReport"]["sha256"] == visual_digest
    assert manifest["visualReport"]["sidecarVerified"] is True
    assert manifest["liveClamdReport"]["sidecarVerified"] is False
    assert manifest["visualReport"]["visualFactsCount"] == 4
    assert manifest["liveClamdReport"]["cases"][0]["name"] == "benign_png"
    assert sidecar["reportSha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_manifest_builder_fails_closed_on_visual_sidecar_mismatch(tmp_path: Path) -> None:
    visual_report = tmp_path / "real.json"
    live_report = tmp_path / "live.json"
    output = tmp_path / "bundle.json"
    _write_json(visual_report, _visual_report())
    _write_json(live_report, _live_report())
    visual_report.with_suffix(".json.sha256").write_text(
        json.dumps({"schemaVersion": "1.0", "reportSha256": "0" * 64}) + "\n",
        encoding="utf-8",
    )

    assert (
        bundle.main(
            [
                "--visual-report",
                str(visual_report),
                "--live-clamd-report",
                str(live_report),
                "--output",
                str(output),
            ]
        )
        == 1
    )

    manifest = json.loads(output.read_text())
    assert manifest["status"] == "FAIL"
    assert "sidecar sha256 mismatch" in manifest["reason"]


def test_manifest_builder_fails_closed_on_incomplete_live_cases(tmp_path: Path) -> None:
    visual_report = tmp_path / "real.json"
    live_report = tmp_path / "live.json"
    output = tmp_path / "bundle.json"
    _write_json(visual_report, _visual_report())
    broken_live = _live_report()
    broken_live["cases"] = broken_live["cases"][:-1]
    _write_json(live_report, broken_live)

    assert (
        bundle.main(
            [
                "--visual-report",
                str(visual_report),
                "--live-clamd-report",
                str(live_report),
                "--output",
                str(output),
            ]
        )
        == 1
    )

    manifest = json.loads(output.read_text())
    assert manifest["status"] == "FAIL"
    assert "cases are incomplete" in manifest["reason"]
