from __future__ import annotations

from collections.abc import Mapping, Sequence
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Any, ClassVar, Final

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

def _last_probe_json(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        payload = json.loads(candidate)
        assert isinstance(payload, dict)
        return payload
    raise AssertionError("probe did not emit JSON")


class FakeTextContent:
    def __init__(self, *, text: str) -> None:
        self.text = text


class FakeMessageToolCall:
    def __init__(self, *, id: str, name: str, arguments: str, origin: str) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments
        self.origin = origin


class FakeMessage:
    def __init__(
        self,
        *,
        role: str,
        content: Sequence[FakeTextContent],
        tool_calls: Sequence[FakeMessageToolCall] | None = None,
    ) -> None:
        self.role = role
        self.content = list(content)
        self.tool_calls = list(tool_calls or [])


class FakeLLM:
    pass


class FakeTestLLM(FakeLLM):
    def __init__(self, messages: Sequence[FakeMessage], *, usage_id: str) -> None:
        self.messages = list(messages)
        self.usage_id = usage_id

    @classmethod
    def from_messages(
        cls,
        messages: list[FakeMessage | Exception],
        *,
        usage_id: str = "test-llm",
        **_: object,
    ) -> "FakeTestLLM":
        fake_messages = [message for message in messages if isinstance(message, FakeMessage)]
        return cls(fake_messages, usage_id=usage_id)


class FakeAgent:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeEventLog:
    pass


class FakeToolDefinition:
    pass


class FakeToolExecutor:
    pass


class FakeFinishAction:
    def __init__(self, *, message: str) -> None:
        self.message = message


class FakeFinishObservation:
    def __init__(self, *, text: str = "done") -> None:
        self.text = text

    @classmethod
    def from_text(cls, *, text: str) -> "FakeFinishObservation":
        return cls(text=text)


class FakeActionEvent:
    constructor_calls: ClassVar[int] = 0

    def __init__(
        self,
        *,
        id: str,
        thought: Sequence[FakeTextContent],
        action: FakeFinishAction,
        tool_name: str,
        tool_call_id: str,
        tool_call: FakeMessageToolCall,
        llm_response_id: str,
        _native: bool = False,
        **_: object,
    ) -> None:
        if not _native:
            type(self).constructor_calls += 1
        self.id = id
        self.thought = list(thought)
        self.action = action
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.tool_call = tool_call
        self.llm_response_id = llm_response_id

    def model_dump_json(self, *, exclude_none: bool = True) -> str:
        del exclude_none
        return json.dumps(
            {
                "id": self.id,
                "kind": type(self).__name__,
                "llm_response_id": self.llm_response_id,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
            },
            sort_keys=True,
        )


class FakeObservationEvent:
    constructor_calls: ClassVar[int] = 0

    def __init__(
        self,
        *,
        id: str,
        tool_name: str,
        tool_call_id: str,
        observation: object,
        action_id: str,
        _native: bool = False,
        **_: object,
    ) -> None:
        if not _native:
            type(self).constructor_calls += 1
        self.id = id
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.observation = observation
        self.action_id = action_id

    def model_dump_json(self, *, exclude_none: bool = True) -> str:
        del exclude_none
        return json.dumps(
            {
                "action_id": self.action_id,
                "id": self.id,
                "kind": type(self).__name__,
                "observation": type(self.observation).__name__,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
            },
            sort_keys=True,
        )


class FakeState:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    @property
    def events(self) -> list[object]:
        FakeLocalConversation.events_accesses += 1
        return self._events


class FakeLocalConversation:
    arun_calls: ClassVar[int] = 0
    close_calls: ClassVar[int] = 0
    events_accesses: ClassVar[int] = 0
    scenario: ClassVar[str] = "happy"

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        max_iteration_per_run = kwargs["max_iteration_per_run"]
        assert isinstance(max_iteration_per_run, int)
        self.max_iteration_per_run = max_iteration_per_run
        self._events: list[object] = []
        self.state = FakeState(self._events)

    async def arun(self) -> None:
        type(self).arun_calls += 1
        action_id = (
            "" if type(self).scenario == "empty_ids" else "event_action_ai4c"
        )
        action_tool_call_id = (
            "" if type(self).scenario == "empty_ids" else "call_ai4c_finish"
        )
        tool_call_id = (
            "call_ai4c_wrong"
            if type(self).scenario == "tool_call_id_mismatch"
            else action_tool_call_id
        )
        self._events.extend(
            [
                FakeActionEvent(
                    id=action_id,
                    thought=[FakeTextContent(text="finish deterministically")],
                    action=FakeFinishAction(message="done"),
                    tool_name="finish",
                    tool_call_id=action_tool_call_id,
                    tool_call=FakeMessageToolCall(
                        id=tool_call_id,
                        name="finish",
                        arguments=json.dumps({"message": "done"}),
                        origin="completion",
                    ),
                    llm_response_id="response_ai4c",
                    _native=True,
                ),
                FakeObservationEvent(
                    id="event_observation_ai4c",
                    tool_name="finish",
                    tool_call_id=action_tool_call_id,
                    observation=FakeFinishObservation(text="done"),
                    action_id=action_id,
                    _native=True,
                ),
            ]
        )

    def interrupt(self) -> None:
        pass

    def close(self) -> None:
        type(self).close_calls += 1


def _install_fake_openhands_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    module_names = (
        "openhands",
        "openhands.sdk",
        "openhands.sdk.conversation",
        "openhands.sdk.event",
        "openhands.sdk.llm",
        "openhands.sdk.testing",
        "openhands.sdk.tool",
        "openhands.sdk.tool.builtins",
        "openhands.sdk.tool.builtins.finish",
    )
    modules = {name: ModuleType(name) for name in module_names}
    setattr(modules["openhands.sdk"], "Agent", FakeAgent)
    setattr(modules["openhands.sdk"], "LLM", FakeLLM)
    setattr(modules["openhands.sdk.conversation"], "EventLog", FakeEventLog)
    setattr(modules["openhands.sdk.conversation"], "LocalConversation", FakeLocalConversation)
    setattr(modules["openhands.sdk.event"], "ActionEvent", FakeActionEvent)
    setattr(modules["openhands.sdk.event"], "ObservationEvent", FakeObservationEvent)
    setattr(modules["openhands.sdk.llm"], "Message", FakeMessage)
    setattr(modules["openhands.sdk.llm"], "MessageToolCall", FakeMessageToolCall)
    setattr(modules["openhands.sdk.llm"], "TextContent", FakeTextContent)
    setattr(modules["openhands.sdk.testing"], "TestLLM", FakeTestLLM)
    setattr(modules["openhands.sdk.tool"], "ToolDefinition", FakeToolDefinition)
    setattr(modules["openhands.sdk.tool"], "ToolExecutor", FakeToolExecutor)
    setattr(modules["openhands.sdk.tool.builtins.finish"], "FinishAction", FakeFinishAction)
    setattr(modules["openhands.sdk.tool.builtins.finish"], "FinishObservation", FakeFinishObservation)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


def _execute_probe_source_with_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    scenario: str = "happy",
) -> dict[str, object]:
    _install_fake_openhands_modules(monkeypatch)
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda distribution: "1.31.0" if distribution == "openhands-sdk" else "0",
    )
    monkeypatch.setattr(sys, "argv", ["probe", "1.31.0"])
    FakeLocalConversation.arun_calls = 0
    FakeLocalConversation.close_calls = 0
    FakeLocalConversation.events_accesses = 0
    FakeLocalConversation.scenario = scenario
    FakeActionEvent.constructor_calls = 0
    FakeObservationEvent.constructor_calls = 0

    try:
        exec(equivalence.PROBE_SOURCE, {"__name__": "__main__"})
    except SystemExit as exc:
        assert exc.code in (0, None)
    return _last_probe_json(capsys.readouterr().out)



def test_probe_source_executes_native_arun_and_serializes_state_events(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _execute_probe_source_with_fake_sdk(monkeypatch, capsys)

    assert payload["result"] == "PASS"
    assert FakeLocalConversation.arun_calls == 1
    assert FakeLocalConversation.events_accesses >= 1
    assert FakeLocalConversation.close_calls == 1
    assert FakeActionEvent.constructor_calls == 0
    assert FakeObservationEvent.constructor_calls == 0
    assert isinstance(payload["signature_digest"], str)
    assert isinstance(payload["lifecycle_digest"], str)
    assert isinstance(payload["event_digest"], str)


@pytest.mark.parametrize(
    ("scenario", "expected_reason"),
    [
        ("empty_ids", "event_identity_missing"),
        ("tool_call_id_mismatch", "event_tool_call_mismatch"),
    ],
)
def test_probe_source_blocks_invalid_event_linkage_from_state_events(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
    expected_reason: str,
) -> None:
    payload = _execute_probe_source_with_fake_sdk(
        monkeypatch,
        capsys,
        scenario=scenario,
    )

    assert payload["result"] == "BLOCKED"
    assert payload["reason_codes"] == [expected_reason]
    assert FakeLocalConversation.arun_calls == 1
    assert FakeLocalConversation.events_accesses >= 1


def test_probe_source_blocks_empty_ids_from_official_native_events(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import openhands.sdk.conversation as sdk_conversation
    from openhands.sdk.event import ActionEvent as SDKActionEvent
    from openhands.sdk.event import ObservationEvent as SDKObservationEvent
    from openhands.sdk.llm import MessageToolCall as SDKMessageToolCall
    from openhands.sdk.llm import TextContent as SDKTextContent
    from openhands.sdk.tool.builtins.finish import FinishAction as SDKFinishAction
    from openhands.sdk.tool.builtins.finish import (
        FinishObservation as SDKFinishObservation,
    )

    class NativeState:
        def __init__(self) -> None:
            self.events: list[object] = []

    class NativeEmptyIdConversation:
        def __init__(self, **kwargs: object) -> None:
            max_iteration_per_run = kwargs["max_iteration_per_run"]
            assert isinstance(max_iteration_per_run, int)
            self.max_iteration_per_run = max_iteration_per_run
            self.state = NativeState()

        async def arun(self) -> None:
            self.state.events.extend(
                [
                    SDKActionEvent(
                        id="",
                        thought=[SDKTextContent(text="finish deterministically")],
                        action=SDKFinishAction(message="done"),
                        tool_name="finish",
                        tool_call_id="",
                        tool_call=SDKMessageToolCall(
                            id="",
                            name="finish",
                            arguments=json.dumps({"message": "done"}),
                            origin="completion",
                        ),
                        llm_response_id="response_ai4c",
                    ),
                    SDKObservationEvent(
                        id="event_observation_ai4c",
                        tool_name="finish",
                        tool_call_id="",
                        observation=SDKFinishObservation.from_text(text="done"),
                        action_id="",
                    ),
                ]
            )

        def interrupt(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(sdk_conversation, "LocalConversation", NativeEmptyIdConversation)
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda distribution: "1.31.0" if distribution == "openhands-sdk" else "0",
    )
    monkeypatch.setattr(sys, "argv", ["probe", "1.31.0"])

    try:
        exec(equivalence.PROBE_SOURCE, {"__name__": "__main__"})
    except SystemExit as exc:
        assert exc.code in (0, None)
    payload = _last_probe_json(capsys.readouterr().out)

    assert payload["result"] == "BLOCKED"
    assert payload["reason_codes"] == ["event_identity_missing"]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "result": "PASS",
            "signature_digest": "a" * 64,
            "lifecycle_digest": "b" * 64,
            "event_digest": "c" * 64,
            "reason_codes": [],
        },
        {
            "version": None,
            "result": "PASS",
            "signature_digest": "a" * 64,
            "lifecycle_digest": "b" * 64,
            "event_digest": "c" * 64,
            "reason_codes": [],
        },
        {
            "version": 1,
            "result": "PASS",
            "signature_digest": "a" * 64,
            "lifecycle_digest": "b" * 64,
            "event_digest": "c" * 64,
            "reason_codes": [],
        },
        {
            "version": True,
            "result": "PASS",
            "signature_digest": "a" * 64,
            "lifecycle_digest": "b" * 64,
            "event_digest": "c" * 64,
            "reason_codes": [],
        },
        {
            "version": "1.31.1",
            "result": "PASS",
            "signature_digest": "a" * 64,
            "lifecycle_digest": "b" * 64,
            "event_digest": "c" * 64,
            "reason_codes": [],
        },
        {
            "version": "1.31.0",
            "result": "PASS",
            "signature_digest": None,
            "lifecycle_digest": "b" * 64,
            "event_digest": "c" * 64,
            "reason_codes": [],
        },
        {
            "version": "1.31.0",
            "result": "PASS",
            "signature_digest": "not-a-digest",
            "lifecycle_digest": "b" * 64,
            "event_digest": "c" * 64,
            "reason_codes": [],
        },
        {
            "version": "1.31.0",
            "result": "PASS",
            "signature_digest": "a" * 64,
            "lifecycle_digest": "b" * 64,
            "event_digest": "c" * 64,
            "reason_codes": ["unexpected_reason"],
        },
    ],
)
def test_parse_probe_payload_fail_closed_for_invalid_pass_payload(
    payload: Mapping[str, object],
) -> None:
    report = equivalence._parse_probe_payload(payload)

    assert report.result == "BLOCKED"
    assert report.signature_digest is None
    assert report.lifecycle_digest is None
    assert report.event_digest is None
    assert report.reasons == ("probe_invalid_pass_payload",)


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
