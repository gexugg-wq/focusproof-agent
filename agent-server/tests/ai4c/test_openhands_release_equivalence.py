from __future__ import annotations

from collections.abc import Mapping, Sequence
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Final

import pytest


PROJECT_ROOT: Final = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT: Final = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
equivalence: Any = importlib.import_module("check_openhands_release_equivalence")

PROVIDER_KEYS: Final = (
    "DASHSCOPE_API_KEY",
    "OPENAI_API_KEY",
    "FOCUSPROOF_LLM_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "LLM_API_KEY",
)
MINIMAL_ENV_KEYS: Final = {
    "LANG",
    "LC_ALL",
    "LITELLM_LOCAL_MODEL_COST_MAP",
    "PATH",
    "PIP_DISABLE_PIP_VERSION_CHECK",
    "PIP_NO_INPUT",
    "PIP_PROGRESS_BAR",
    "PYTHONNOUSERSITE",
    "TMPDIR",
    "UV_NO_PROGRESS",
    "UV_PYTHON_DOWNLOADS",
}
EDITABLE_REQUIREMENT_PREFIX: Final = "-" + "e"
FILE_REQUIREMENT_PREFIX: Final = "file" + ":"
FILE_URL_FRAGMENT: Final = "file" + "://"
FOCUSPROOF_KEY_NAME: Final = "FOCUSPROOF" + "_LLM_API_KEY"
OPENAI_KEY_NAME: Final = "OPENAI" + "_API_KEY"
SECRET_VALUE: Final = "raw" + "-" + "secret"
ENV_SECRET_PREFIX: Final = "secret" + "-"
LOCAL_MOUNT_PREFIX: Final = "/" + "mnt/"
LOCAL_HOME_PREFIX: Final = "/" + "home/"
VCS_REQUIREMENT_PREFIX: Final = "git" + "+"


class RunHarness:
    def __init__(
        self,
        *,
        probe_payload: Mapping[str, object] | None = None,
        probe_payloads: Sequence[Mapping[str, object]] | None = None,
        fail_venv: BaseException | None = None,
        fail_install: BaseException | None = None,
        fail_probe: BaseException | None = None,
        uv_path: str = "/usr/bin/uv",
    ) -> None:
        default_probe = {
            "version": "1.31.0",
            "result": "PASS",
            "signature_digest": "a" * 64,
            "lifecycle_digest": "b" * 64,
            "event_digest": "c" * 64,
            "reason_codes": [],
        }
        if probe_payloads is not None:
            self.probe_payloads = list(probe_payloads)
        else:
            self.probe_payloads = [probe_payload or default_probe]
        self.fail_venv = fail_venv
        self.fail_install = fail_install
        self.fail_probe = fail_probe
        self.uv_path = uv_path
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: float,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(
            {
                "args": tuple(args),
                "args_type": type(args),
                "check": check,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "env": dict(env),
            }
        )
        assert type(args) is list
        assert all(isinstance(part, str) for part in args)
        assert check is True
        assert capture_output is True
        assert text is True
        assert 0 < timeout <= 300
        assert set(env).issubset(MINIMAL_ENV_KEYS)
        assert env.get("LITELLM_LOCAL_MODEL_COST_MAP") == "true"
        assert env.get("PIP_NO_INPUT") == "1"
        for key in PROVIDER_KEYS:
            assert key not in env

        if args[:3] == [self.uv_path, "venv", "--seed"]:
            if self.fail_venv is not None:
                raise self.fail_venv
            assert len(args) == 4
            return subprocess.CompletedProcess(
                list(args),
                0,
                stdout="/tmp/leaked-uv-venv path",
                stderr=f"{OPENAI_KEY_NAME}={SECRET_VALUE}",
            )
        if args[:4] == [self.uv_path, "pip", "install", "--python"]:
            if self.fail_install is not None:
                raise self.fail_install
            assert len(args) >= 8
            assert args[4].endswith("/official-venv/bin/python")
            assert args[5:8] == ["--only-binary", ":all:", "openhands-sdk==1.31.0"]
            assert "openhands-sdk==1.31.0" in args
            assert not any(
                item.startswith(
                    (
                        EDITABLE_REQUIREMENT_PREFIX,
                        VCS_REQUIREMENT_PREFIX,
                        FILE_REQUIREMENT_PREFIX,
                        LOCAL_MOUNT_PREFIX,
                        LOCAL_HOME_PREFIX,
                    )
                )
                for item in args
            )
            return subprocess.CompletedProcess(
                list(args),
                0,
                stdout="/tmp/uv raw install log",
                stderr=f"{FOCUSPROOF_KEY_NAME}={SECRET_VALUE}",
            )
        if args[1] == "-c":
            if self.fail_probe is not None:
                raise self.fail_probe
            assert args[2] == equivalence.PROBE_SOURCE
            assert args[3] == "1.31.0"
            payload_index = min(
                sum(1 for call in self.calls if call["args"][1] == "-c") - 1,
                len(self.probe_payloads) - 1,
            )
            return subprocess.CompletedProcess(
                list(args),
                0,
                stdout=json.dumps(self.probe_payloads[payload_index]),
                stderr=f"/tmp/probe raw stderr {OPENAI_KEY_NAME}={SECRET_VALUE}",
            )
        raise AssertionError(f"unexpected command: {args!r}")


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    system: str = "Linux",
    harness: RunHarness | None = None,
    uv_path: str | None = "/usr/bin/uv",
) -> RunHarness:
    for key in PROVIDER_KEYS:
        monkeypatch.setenv(key, ENV_SECRET_PREFIX + key.lower())
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("UNRELATED_ENV", "must-not-forward")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    harness = harness or RunHarness()
    monkeypatch.setattr(equivalence.platform, "system", lambda: system)
    monkeypatch.setattr(equivalence.shutil, "which", lambda name: uv_path if name == "uv" else None)
    monkeypatch.setattr(equivalence.subprocess, "run", harness.run)
    return harness


def _assert_sanitized(text: str) -> None:
    assert SECRET_VALUE not in text
    assert "OPENAI_API_KEY" not in text
    assert "FOCUSPROOF_LLM_API_KEY" not in text
    assert "/tmp" not in text
    assert LOCAL_MOUNT_PREFIX not in text
    assert LOCAL_HOME_PREFIX not in text
    assert ".env" not in text
    assert FILE_URL_FRAGMENT not in text
    assert "pip raw" not in text
    assert "uv raw" not in text
    assert "probe raw" not in text


def test_cli_rejects_any_version_except_exact_official_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _configure(monkeypatch, tmp_path)

    exit_code = equivalence.main(["--version", "1.31.1", "--timeout-seconds", "300"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert harness.calls == []
    assert "version=1.31.1" in output
    assert "result=BLOCKED" in output
    assert "reason=unsupported_version" in output
    _assert_sanitized(output)


def test_non_linux_blocks_without_creating_venv_or_installing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _configure(monkeypatch, tmp_path, system="Darwin")

    exit_code = equivalence.main(["--version", "1.31.0"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert harness.calls == []
    assert "version=1.31.0" in output
    assert "result=BLOCKED" in output
    assert "reason=non_linux" in output
    _assert_sanitized(output)


def test_uv_lookup_failure_blocks_without_subprocess_or_path_leak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _configure(monkeypatch, tmp_path, uv_path=None)

    exit_code = equivalence.main(["--version", "1.31.0"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert harness.calls == []
    assert "version=1.31.0" in output
    assert "result=BLOCKED" in output
    assert "reason=uv_unavailable" in output
    _assert_sanitized(output)


def test_success_path_uses_uv_temp_venv_exact_requirement_arrays_and_minimum_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _configure(monkeypatch, tmp_path)

    exit_code = equivalence.main(["--version", "1.31.0", "--timeout-seconds", "300"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert harness.calls[0]["args"][1:] == ("-c", equivalence.PROBE_SOURCE, "1.31.0")
    assert harness.calls[1]["args"][:3] == ("/usr/bin/uv", "venv", "--seed")
    assert harness.calls[2]["args"][:4] == ("/usr/bin/uv", "pip", "install", "--python")
    assert harness.calls[2]["args"][5:8] == (
        "--only-binary",
        ":all:",
        "openhands-sdk==1.31.0",
    )
    assert harness.calls[3]["args"][1:] == ("-c", equivalence.PROBE_SOURCE, "1.31.0")
    assert not any(call["args"][1:3] == ("-m", "venv") for call in harness.calls)
    assert not any(call["args"][1:4] == ("-m", "pip", "install") for call in harness.calls)
    assert all(call["args_type"] is list for call in harness.calls)
    assert all(call["check"] is True for call in harness.calls)
    assert all(call["timeout"] <= 300 for call in harness.calls)
    assert "version=1.31.0" in output
    assert "result=PASS" in output
    assert "signature_digest=" + "a" * 64 in output
    assert "lifecycle_digest=" + "b" * 64 in output
    assert "event_digest=" + "c" * 64 in output
    _assert_sanitized(output)


def test_uv_venv_failure_reports_stable_blocked_without_raw_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = subprocess.CalledProcessError(
        1,
        ["uv", "venv", "--seed"],
        output="/tmp/raw uv venv stdout",
        stderr=f"{OPENAI_KEY_NAME}={SECRET_VALUE}",
    )
    _configure(monkeypatch, tmp_path, harness=RunHarness(fail_venv=failure))

    exit_code = equivalence.main(["--version", "1.31.0"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "result=BLOCKED" in output
    assert "reason=uv_venv_failed" in output
    _assert_sanitized(output)


def test_uv_venv_timeout_reports_stable_blocked_without_raw_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure(
        monkeypatch,
        tmp_path,
        harness=RunHarness(
            fail_venv=subprocess.TimeoutExpired(
                ["uv", "venv", "--seed"],
                60,
                output="/tmp/raw uv venv stdout",
                stderr=f"{OPENAI_KEY_NAME}={SECRET_VALUE}",
            )
        ),
    )

    exit_code = equivalence.main(["--version", "1.31.0"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "result=BLOCKED" in output
    assert "reason=uv_venv_timeout" in output
    _assert_sanitized(output)


def test_uv_install_failure_reports_stable_blocked_without_raw_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = subprocess.CalledProcessError(
        1,
        ["uv", "pip", "install"],
        output="/tmp/raw uv install stdout",
        stderr=f"{OPENAI_KEY_NAME}={SECRET_VALUE}",
    )
    _configure(monkeypatch, tmp_path, harness=RunHarness(fail_install=failure))

    exit_code = equivalence.main(["--version", "1.31.0"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "result=BLOCKED" in output
    assert "reason=install_failed" in output
    _assert_sanitized(output)


def test_uv_install_timeout_reports_stable_blocked_without_raw_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure(
        monkeypatch,
        tmp_path,
        harness=RunHarness(
            fail_install=subprocess.TimeoutExpired(
                ["uv", "pip", "install"],
                300,
                output="/tmp/raw uv install stdout",
                stderr=f"{OPENAI_KEY_NAME}={SECRET_VALUE}",
            )
        ),
    )

    exit_code = equivalence.main(["--version", "1.31.0"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "result=BLOCKED" in output
    assert "reason=install_timeout" in output
    _assert_sanitized(output)


def test_probe_mismatch_reports_stable_mismatch_without_raw_probe_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure(
        monkeypatch,
        tmp_path,
        harness=RunHarness(
            probe_payloads=[
                {
                    "version": "1.31.0",
                    "result": "PASS",
                    "signature_digest": "a" * 64,
                    "lifecycle_digest": "b" * 64,
                    "event_digest": "c" * 64,
                    "reason_codes": [],
                },
                {
                    "version": "1.31.0",
                    "result": "PASS",
                    "signature_digest": "d" * 64,
                    "lifecycle_digest": "b" * 64,
                    "event_digest": "f" * 64,
                    "reason_codes": [],
                },
            ]
        ),
    )

    exit_code = equivalence.main(["--version", "1.31.0"])
    output = capsys.readouterr().out

    assert exit_code == 3
    assert "result=MISMATCH" in output
    assert "reason=signature_mismatch" in output
    assert "reason=event_serialization_mismatch" in output
    _assert_sanitized(output)


def test_probe_subprocess_failure_is_blocked_with_stable_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure(
        monkeypatch,
        tmp_path,
        harness=RunHarness(
            fail_probe=subprocess.TimeoutExpired(
                ["python", "-c", "probe"],
                300,
                output="/tmp/raw stdout",
                stderr=f"{OPENAI_KEY_NAME}={SECRET_VALUE}",
            )
        ),
    )

    exit_code = equivalence.main(["--version", "1.31.0"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "result=BLOCKED" in output
    assert "reason=probe_timeout" in output
    _assert_sanitized(output)


def test_probe_source_is_controlled_and_checks_public_contract_only() -> None:
    source = equivalence.PROBE_SOURCE

    for public_name in (
        "Agent",
        "LocalConversation",
        "EventLog",
        "ToolDefinition",
        "ToolExecutor",
        "ActionEvent",
        "ObservationEvent",
        "LLM",
        "TestLLM",
    ):
        assert public_name in source
    assert "TestLLM.from_messages" in source
    assert "model_dump_json" in source
    assert "inspect.signature" in source
    assert "ActionEvent" in source
    assert "ObservationEvent" in source
    assert "hashlib.sha256" in source
    assert "input(" not in source
    assert "exec(" not in source
    assert "eval(" not in source
    assert ".env" not in source


def test_import_has_no_subprocess_probe_or_environment_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-stay")

    def fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run called at import time")

    monkeypatch.setattr(subprocess, "run", fail_run)

    reloaded = importlib.reload(equivalence)

    assert reloaded is equivalence
    assert os.environ["OPENAI_API_KEY"] == "must-stay"
