from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.real_llm
def test_real_image_provider(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[3]
    report_path = tmp_path / "real-image-provider.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "run_real_visual_provider_gate.py"),
            "--execute-real-provider",
            "--report",
            str(report_path),
            "--scanner-mode",
            "fake-clean",  # Local media-chain gate only; not a production malware-scan claim.
            "--image",
            str(project_root / "docs/research/assets/ai5/task7/chromium-success.png"),
            "--provider",
            "openai",
            "--model",
            "qwen3.7-plus",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    sidecar_path = report_path.with_suffix(report_path.suffix + ".sha256")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["checks"]["officialConversationUsed"] is True
    assert report["checks"]["realRuntimeUsed"] is True
    assert report["checks"]["visionActive"] is True
    assert report["checks"]["mediaToolUsed"] is True
    assert report["checks"]["monadDisabled"] is True
    assert report["checks"]["productionLlmUsed"] is True
    assert report["provider"] == "openai"
    assert report["model"] == "qwen3.7-plus"
    assert report["scanner_mode"] == "fake-clean"
    assert report["assurance"] == {
        "mediaScanner": "fake-clean",
        "scope": "local-test-only",
        "productionMalwareScanningVerified": False,
    }
    assert report["image"] == {
        "path": str(
            (project_root / "docs/research/assets/ai5/task7/chromium-success.png").resolve()
        ),
        "size": 93296,
        "sha256": "7ee186d8b0efa5ca62039ab97655e811e748c86696fee1752f8c0fc7ef3f468e",
    }
    assert report["conversationId"]
    assert "ActionEvent" in report["eventTypes"]
    assert report["nativeEvents"]["actionCount"] > 0
    assert report["nativeEvents"]["observationCount"] > 0
    assert report["diagnostics"]["providerAttempted"] is True
    assert report["diagnostics"]["responseReceived"] is True
    assert report["diagnostics"]["completionSucceeded"] is True
    assert report["diagnostics"]["totalProviderCompletionCalls"] >= 1
    assert report["diagnostics"]["visualProviderCompletionCalls"] == 1
    assert report["diagnostics"]["agentDecisionCompletionCalls"] == (
        report["diagnostics"]["totalProviderCompletionCalls"] - 1
    )
    assert report["diagnostics"]["visualProviderAttempted"] is True
    assert report["diagnostics"]["visualProviderResponseReceived"] is True
    assert report["diagnostics"]["visualProviderCompletionSucceeded"] is True
    assert report["diagnostics"]["visualResponseFormat"] in {
        "plain_json",
        "fenced_json",
    }
    assert report["diagnostics"]["visualResponseParseStage"] == "complete"
    assert report["diagnostics"]["visualProviderErrorCategory"] == "none"
    assert report["diagnostics"]["responseTextLength"] > 0
    assert report["diagnostics"]["visualFactsParsed"] is True
    assert report["diagnostics"]["visualFactsCount"] >= 3
    assert report["diagnostics"]["reviewStatus"] == "completed"
    assert sidecar == {
        "schemaVersion": "1.0",
        "reportSha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "runnerSourceSha256": report["runnerSourceSha256"],
    }
    serialized = json.dumps(report).lower()
    assert "data:image" not in serialized
    assert "base64," not in serialized
    assert "api_key" not in serialized
