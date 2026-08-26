from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

import scripts.run_real_image_evidence_gate as gate


def _passing_cases() -> tuple[gate.GateCaseResult, ...]:
    return tuple(
        gate.GateCaseResult(name=name, outcome=outcome, passed=True)
        for name, outcome in (
            ("benign_png", "clean"),
            ("eicar", "malicious"),
            ("timeout", "timeout"),
            ("unavailable", "unavailable"),
            ("error", "error"),
        )
    )


def test_certification_is_live_only_and_keeps_all_model_providers_off() -> None:
    report = gate.build_report(_passing_cases(), live_clamd_executed=True)

    assert report["gate"] == "production_clamd"
    assert report["liveClamdExecuted"] is True
    assert report["visualProviderEnabled"] is False
    assert report["productionLlmEnabled"] is False
    assert report["productionMalwareScanningVerified"] is True


@pytest.mark.parametrize("failed_index", range(5))
def test_certification_remains_false_until_every_case_passes(failed_index: int) -> None:
    cases = list(_passing_cases())
    cases[failed_index] = replace(cases[failed_index], passed=False)

    report = gate.build_report(tuple(cases), live_clamd_executed=True)

    assert report["productionMalwareScanningVerified"] is False


def test_certification_remains_false_when_live_clamd_was_not_executed() -> None:
    report = gate.build_report(_passing_cases(), live_clamd_executed=False)
    assert report["productionMalwareScanningVerified"] is False


def test_report_has_exact_required_matrix_and_no_sensitive_values() -> None:
    report = gate.build_report(_passing_cases(), live_clamd_executed=True)
    encoded = json.dumps(report, sort_keys=True)

    assert [case["name"] for case in report["cases"]] == [
        "benign_png",
        "eicar",
        "timeout",
        "unavailable",
        "error",
    ]
    for forbidden in ("endpoint", "password", "token", "private", "eicar.com"):
        assert forbidden not in encoded.lower()


def test_cli_without_explicit_live_endpoint_fails_closed(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    exit_code = gate.main(["--report", str(report_path)])

    assert exit_code != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["productionMalwareScanningVerified"] is False
    assert report["reasonCode"] == "live_clamd_not_configured"


def test_source_does_not_read_environment_or_import_visual_runtime() -> None:
    source = Path(gate.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "load_project_env" not in source
    assert "openhands" not in source.lower()
    assert "fake-clean" not in source


def test_fault_harness_is_bounded_and_cleanup_is_explicit() -> None:
    source = Path(gate.__file__).read_text(encoding="utf-8")
    assert "settimeout" in source
    assert "join(timeout=" in source
    assert "finally:" in source
    assert "close()" in source


def test_existing_staging_compose_wires_pinned_private_clamd_sidecar() -> None:
    compose = (gate.ROOT / "deploy" / "compose.staging.yml").read_text(encoding="utf-8")

    assert "clamd:" in compose
    assert (
        "clamav/clamav:1.4.3@"
        "sha256:75fb5fd95fcbe1d7e6d240c369c1572b686ee2c95949d1042b5148de8eddebb4" in compose
    )
    assert "FOCUSPROOF_CLAMD_ENDPOINT: tcp://clamd:3310" in compose
    assert "3310:3310" not in compose
