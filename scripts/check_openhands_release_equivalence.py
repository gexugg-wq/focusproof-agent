from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Literal


OFFICIAL_VERSION = "1.31.0"
OFFICIAL_REQUIREMENT = f"openhands-sdk=={OFFICIAL_VERSION}"
ResultState = Literal["PASS", "BLOCKED", "MISMATCH"]
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
REASON_RE = re.compile(r"^[a-z0-9_:-]+$")
PROVIDER_KEYS = frozenset(
    (
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
)
MINIMAL_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TMPDIR")

PROBE_SOURCE = r"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import inspect
import io
import json
import sys
import tempfile
from pathlib import Path


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _blocked(version: str, reason: str) -> None:
    print(
        json.dumps(
            {
                "version": version,
                "result": "BLOCKED",
                "signature_digest": None,
                "lifecycle_digest": None,
                "event_digest": None,
                "reason_codes": [reason],
            },
            sort_keys=True,
        )
    )


expected_version = sys.argv[1]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        from openhands.sdk import Agent, LLM
        from openhands.sdk.conversation import EventLog, LocalConversation
        from openhands.sdk.event import ActionEvent, ObservationEvent
        from openhands.sdk.llm import Message, MessageToolCall, TextContent
        from openhands.sdk.testing import TestLLM
        from openhands.sdk.tool import ToolDefinition, ToolExecutor
        from openhands.sdk.tool.builtins.finish import FinishAction, FinishObservation

    version = importlib.metadata.version("openhands-sdk")
    if version != expected_version:
        print(
            json.dumps(
                {
                    "version": version,
                    "result": "MISMATCH",
                    "signature_digest": None,
                    "lifecycle_digest": None,
                    "event_digest": None,
                    "reason_codes": ["version_mismatch"],
                },
                sort_keys=True,
            )
        )
        raise SystemExit(0)

    signatures = {
        "Agent": str(inspect.signature(Agent)),
        "LLM": str(inspect.signature(LLM)),
        "LocalConversation": str(inspect.signature(LocalConversation)),
        "LocalConversation.arun": str(inspect.signature(LocalConversation.arun)),
        "LocalConversation.close": str(inspect.signature(LocalConversation.close)),
        "LocalConversation.interrupt": str(inspect.signature(LocalConversation.interrupt)),
        "EventLog": str(inspect.signature(EventLog)),
        "ToolDefinition": str(inspect.signature(ToolDefinition)),
        "ToolExecutor": str(inspect.signature(ToolExecutor)),
        "ActionEvent": str(inspect.signature(ActionEvent)),
        "ObservationEvent": str(inspect.signature(ObservationEvent)),
        "TestLLM.from_messages": str(inspect.signature(TestLLM.from_messages)),
    }

    with tempfile.TemporaryDirectory() as root:
        root_path = Path(root)
        llm = TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="deterministic")])],
            usage_id="ai4c-release-probe",
        )
        agent = Agent(
            llm=llm,
            tools=[],
            include_default_tools=[],
            system_prompt="AI4C release equivalence deterministic probe.",
        )
        conversation = LocalConversation(
            agent=agent,
            workspace=root_path / "workspace",
            persistence_dir=root_path / "persistence",
            max_iteration_per_run=1,
            visualizer=None,
            delete_on_close=False,
            user_id="ai4c-release-probe",
        )
        try:
            lifecycle = {
                "agent_type": type(agent).__name__,
                "conversation_type": type(conversation).__name__,
                "events_type": type(conversation.state.events).__name__,
                "llm_is_sdk_llm": isinstance(llm, LLM),
                "test_llm_type": type(llm).__name__,
                "has_arun": callable(getattr(conversation, "arun")),
                "has_interrupt": callable(getattr(conversation, "interrupt")),
                "has_close": callable(getattr(conversation, "close")),
                "max_iteration_per_run": conversation.max_iteration_per_run,
            }
        finally:
            conversation.close()

    tool_call = MessageToolCall(
        id="call_ai4c_finish",
        name="FinishTool",
        arguments='{"message":"done"}',
        origin="completion",
    )
    action = ActionEvent(
        id="event_action_ai4c",
        timestamp="2026-07-22T00:00:00",
        thought=[TextContent(text="finish deterministically")],
        action=FinishAction(message="done"),
        tool_name="FinishTool",
        tool_call_id="call_ai4c_finish",
        tool_call=tool_call,
        llm_response_id="response_ai4c",
    )
    observation = ObservationEvent(
        id="event_observation_ai4c",
        timestamp="2026-07-22T00:00:01",
        tool_name="FinishTool",
        tool_call_id="call_ai4c_finish",
        observation=FinishObservation.from_text(text="done"),
        action_id=action.id,
    )
    events = [action, observation]
    event_payload = {
        "sequence": [type(event).__name__ for event in events],
        "tool_call_ids": [action.tool_call_id, observation.tool_call_id],
        "action_before_observation": events.index(action) < events.index(observation),
        "serialized": [
            json.loads(event.model_dump_json(exclude_none=True)) for event in events
        ],
    }
    if event_payload["sequence"] != ["ActionEvent", "ObservationEvent"]:
        _blocked(version, "event_order_failed")
        raise SystemExit(0)
    if action.tool_call_id != observation.tool_call_id:
        _blocked(version, "event_tool_call_mismatch")
        raise SystemExit(0)

    print(
        json.dumps(
            {
                "version": version,
                "result": "PASS",
                "signature_digest": _digest(signatures),
                "lifecycle_digest": _digest(lifecycle),
                "event_digest": _digest(event_payload),
                "reason_codes": [],
            },
            sort_keys=True,
        )
    )
except Exception:
    _blocked(expected_version, "probe_exception")
"""


@dataclass(frozen=True, slots=True)
class EquivalenceReport:
    version: str
    result: ResultState
    signature_digest: str | None
    lifecycle_digest: str | None
    event_digest: str | None
    reasons: tuple[str, ...]


def _minimal_environment(tmpdir: Path | None = None) -> dict[str, str]:
    env = {
        key: value
        for key in MINIMAL_ENV_KEYS
        if (value := os.environ.get(key)) is not None and key not in PROVIDER_KEYS
    }
    if tmpdir is not None:
        env["TMPDIR"] = str(tmpdir)
    env.update(
        {
            "LITELLM_LOCAL_MODEL_COST_MAP": "true",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PIP_PROGRESS_BAR": "off",
            "PYTHONNOUSERSITE": "1",
            "UV_NO_PROGRESS": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        }
    )
    return env


def _run(
    args: Sequence[str],
    *,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )


def _sanitize_reason(reason: object) -> str | None:
    if isinstance(reason, str) and REASON_RE.fullmatch(reason):
        return reason
    return None


def _digest_value(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and HASH_RE.fullmatch(value):
        return value
    return None


def _result_value(payload: Mapping[str, object]) -> ResultState | None:
    value = payload.get("result")
    if value in ("PASS", "BLOCKED", "MISMATCH"):
        return value
    return None


def _extract_probe_json(stdout: str) -> Mapping[str, object] | None:
    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _parse_probe_payload(payload: Mapping[str, object] | None) -> EquivalenceReport:
    if payload is None:
        return EquivalenceReport(
            version=OFFICIAL_VERSION,
            result="BLOCKED",
            signature_digest=None,
            lifecycle_digest=None,
            event_digest=None,
            reasons=("probe_invalid_json",),
        )
    version_value = payload.get("version")
    version = version_value if isinstance(version_value, str) else OFFICIAL_VERSION
    result = _result_value(payload)
    if result is None:
        result = "BLOCKED"
    reasons_value = payload.get("reason_codes")
    reasons: list[str] = []
    if isinstance(reasons_value, list):
        for reason in reasons_value:
            safe_reason = _sanitize_reason(reason)
            if safe_reason is not None:
                reasons.append(safe_reason)
    return EquivalenceReport(
        version=version,
        result=result,
        signature_digest=_digest_value(payload, "signature_digest"),
        lifecycle_digest=_digest_value(payload, "lifecycle_digest"),
        event_digest=_digest_value(payload, "event_digest"),
        reasons=tuple(reasons),
    )


def _run_probe(
    python_executable: Path | str,
    *,
    version: str,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> EquivalenceReport:
    try:
        completed = _run(
            [str(python_executable), "-c", PROBE_SOURCE, version],
            timeout_seconds=timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return EquivalenceReport(
            version=version,
            result="BLOCKED",
            signature_digest=None,
            lifecycle_digest=None,
            event_digest=None,
            reasons=("probe_timeout",),
        )
    except (OSError, subprocess.CalledProcessError):
        return EquivalenceReport(
            version=version,
            result="BLOCKED",
            signature_digest=None,
            lifecycle_digest=None,
            event_digest=None,
            reasons=("probe_failed",),
        )
    return _parse_probe_payload(_extract_probe_json(completed.stdout))


def _create_uv_venv(
    uv_executable: str,
    venv_dir: Path,
    *,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> str | None:
    try:
        _run(
            [uv_executable, "venv", "--seed", str(venv_dir)],
            timeout_seconds=min(timeout_seconds, 60.0),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "uv_venv_timeout"
    except (OSError, subprocess.CalledProcessError):
        return "uv_venv_failed"
    return None


def _install_official_release(
    uv_executable: str,
    python_executable: Path,
    *,
    timeout_seconds: float,
    env: Mapping[str, str],
) -> str | None:
    try:
        _run(
            [
                uv_executable,
                "pip",
                "install",
                "--python",
                str(python_executable),
                "--only-binary",
                ":all:",
                OFFICIAL_REQUIREMENT,
            ],
            timeout_seconds=timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "install_timeout"
    except (OSError, subprocess.CalledProcessError):
        return "install_failed"
    return None


def _blocked(version: str, reason: str) -> EquivalenceReport:
    return EquivalenceReport(
        version=version,
        result="BLOCKED",
        signature_digest=None,
        lifecycle_digest=None,
        event_digest=None,
        reasons=(reason,),
    )


def _compare_reports(local: EquivalenceReport, official: EquivalenceReport) -> EquivalenceReport:
    if local.result != "PASS":
        return _blocked(OFFICIAL_VERSION, "local_probe_failed")
    if official.result == "BLOCKED":
        return official
    if official.result == "MISMATCH":
        return official
    reasons: list[str] = []
    if local.signature_digest != official.signature_digest:
        reasons.append("signature_mismatch")
    if local.lifecycle_digest != official.lifecycle_digest:
        reasons.append("lifecycle_mismatch")
    if local.event_digest != official.event_digest:
        reasons.append("event_serialization_mismatch")
    if official.version != OFFICIAL_VERSION:
        reasons.append("version_mismatch")
    if reasons:
        return EquivalenceReport(
            version=official.version,
            result="MISMATCH",
            signature_digest=official.signature_digest,
            lifecycle_digest=official.lifecycle_digest,
            event_digest=official.event_digest,
            reasons=tuple(reasons),
        )
    return official


def run_release_equivalence(
    *,
    version: str = OFFICIAL_VERSION,
    timeout_seconds: float = 300.0,
) -> EquivalenceReport:
    if version != OFFICIAL_VERSION:
        return _blocked(version, "unsupported_version")
    if platform.system() != "Linux":
        return _blocked(version, "non_linux")
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        return _blocked(version, "uv_unavailable")

    with tempfile.TemporaryDirectory(prefix="ai4c-openhands-release-") as tmp:
        tmp_path = Path(tmp)
        env = _minimal_environment(tmp_path)
        local = _run_probe(
            sys.executable,
            version=version,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if local.result != "PASS":
            return local
        venv_dir = tmp_path / "official-venv"
        reason = _create_uv_venv(
            uv_executable,
            venv_dir,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if reason is not None:
            return _blocked(version, reason)
        official_python = venv_dir / "bin" / "python"
        reason = _install_official_release(
            uv_executable,
            official_python,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if reason is not None:
            return _blocked(version, reason)
        official = _run_probe(
            official_python,
            version=version,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        return _compare_reports(local, official)


def _emit_report(report: EquivalenceReport) -> None:
    print(f"version={report.version}")
    print(f"result={report.result}")
    if report.signature_digest is not None:
        print(f"signature_digest={report.signature_digest}")
    if report.lifecycle_digest is not None:
        print(f"lifecycle_digest={report.lifecycle_digest}")
    if report.event_digest is not None:
        print(f"event_digest={report.event_digest}")
    for reason in report.reasons:
        print(f"reason={reason}")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check official OpenHands SDK release equivalence.",
    )
    parser.add_argument("--version", default=OFFICIAL_VERSION)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    report = run_release_equivalence(
        version=str(args.version),
        timeout_seconds=float(args.timeout_seconds),
    )
    _emit_report(report)
    if report.result == "PASS":
        return 0
    if report.result == "MISMATCH":
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
