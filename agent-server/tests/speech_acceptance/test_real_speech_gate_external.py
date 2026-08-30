from __future__ import annotations

import os
from pathlib import Path

import pytest

import scripts.run_real_speech_gate as gate

pytestmark = pytest.mark.real_asr


def test_authorized_real_speech_gate() -> None:
    required = {
        "chinese": os.environ.get("FOCUSPROOF_REAL_SPEECH_CHINESE"),
        "english": os.environ.get("FOCUSPROOF_REAL_SPEECH_ENGLISH"),
        "mixed": os.environ.get("FOCUSPROOF_REAL_SPEECH_MIXED"),
    }
    if any(not value for value in required.values()):
        pytest.fail("real_asr requires three explicitly configured local speech clips")
    report = Path("/tmp/focusproof-real-speech-report.json")
    assert gate.main(
        [
            "--authorized",
            "--report",
            str(report),
            "--chinese",
            str(required["chinese"]),
            "--english",
            str(required["english"]),
            "--mixed",
            str(required["mixed"]),
        ]
    ) == 0

    payload = gate.read_redacted_report(report)
    assert payload["status"] == "PASS"
    assert payload["providerCallCount"] == 3
