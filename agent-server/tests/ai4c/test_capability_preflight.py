from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError
import importlib
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
capabilities: Any = importlib.import_module("check_ai4c_capabilities")
PROVIDER_KEYS: Final = (
    "DASHSCOPE_API_KEY",
    "OPENAI_API_KEY",
    "FOCUSPROOF_LLM_API_KEY",
    "ANTHROPIC_API_KEY",
)
MINIMAL_ENV_KEYS: Final = {"PATH", "LANG", "LC_ALL"}
ProbeOutcome = str | tuple[str, str] | BaseException


class ProbeHarness:
    def __init__(
        self,
        *,
        available: Sequence[str],
        results: Mapping[tuple[str, ...], ProbeOutcome],
    ) -> None:
        self._available = frozenset(available)
        self._results = results
        self.calls: list[dict[str, Any]] = []

    def which(self, executable: str) -> str | None:
        if executable in self._available:
            return f"/private/bin/{executable}"
        return None

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
        assert 0 < timeout <= 10
        assert set(env).issubset(MINIMAL_ENV_KEYS)
        for key in PROVIDER_KEYS:
            assert key not in env
        outcome = self._results.get(tuple(args))
        if outcome is None:
            raise AssertionError(f"unexpected probe: {args!r}")
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, tuple):
            stdout, stderr = outcome
        else:
            stdout = outcome
            stderr = "/tmp/leaked/path OPENAI_API_KEY=raw-secret"
        return subprocess.CompletedProcess(
            list(args),
            0,
            stdout=stdout,
            stderr=stderr,
        )


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    system: str = "Linux",
    machine: str = "x86_64",
    available: Sequence[str] = ("docker", "psql"),
    results: Mapping[tuple[str, ...], ProbeOutcome] | None = None,
) -> ProbeHarness:
    for key in PROVIDER_KEYS:
        monkeypatch.setenv(key, f"secret-{key.lower()}")
    monkeypatch.setenv("PATH", "/private/bin")
    monkeypatch.setenv("UNRELATED_ENV", "do-not-forward")
    capability_platform = getattr(capabilities, "platform")
    capability_shutil = getattr(capabilities, "shutil")
    capability_subprocess = getattr(capabilities, "subprocess")
    monkeypatch.setattr(capability_platform, "system", lambda: system)
    monkeypatch.setattr(capability_platform, "machine", lambda: machine)
    harness = ProbeHarness(
        available=available,
        results=results
        or {
            ("docker", "--version"): "Docker version 26.1.4, build test",
            ("docker", "compose", "version"): "Docker Compose version v2.29.1",
            ("psql", "--version"): "psql (PostgreSQL) 16.3",
        },
    )
    monkeypatch.setattr(capability_shutil, "which", harness.which)
    monkeypatch.setattr(capability_subprocess, "run", harness.run)
    return harness


def _assert_sanitized(text: str) -> None:
    assert "raw-secret" not in text
    assert "OPENAI_API_KEY" not in text
    assert "/private" not in text
    assert "/tmp" not in text
    assert ".env" not in text


def test_report_shape_is_frozen_and_uses_fixed_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    report = capabilities.detect_capabilities()

    assert report.container_cli == "available"
    assert report.compose == "available"
    assert report.postgres_client == "available"
    assert report.linux_arch == "x86_64"
    assert report.reasons == ()
    assert not hasattr(report, "__dict__")
    with pytest.raises((FrozenInstanceError, AttributeError)):
        setattr(report, "container_cli", "blocked")


def test_non_linux_blocks_all_capabilities_without_version_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure(monkeypatch, system="Darwin", machine="arm64")

    report = capabilities.detect_capabilities()

    assert report.container_cli == "blocked"
    assert report.compose == "blocked"
    assert report.postgres_client == "blocked"
    assert report.linux_arch == "blocked"
    assert report.reasons == ("linux_arch:blocked:unsupported_os",)
    assert harness.calls == []


def test_docker_missing_uses_podman_compatible_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _configure(
        monkeypatch,
        available=("podman", "psql"),
        results={
            ("podman", "--version"): "podman version 5.2.1",
            ("podman", "compose", "version"): "Docker Compose version v2.29.1",
            ("psql", "--version"): "psql (PostgreSQL) 16.3",
        },
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "available"
    assert report.compose == "available"
    assert report.postgres_client == "available"
    assert report.reasons == ()
    assert ("podman", "--version") in [call["args"] for call in harness.calls]


def test_linux_blocks_when_docker_and_podman_are_both_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(
        monkeypatch,
        available=("psql",),
        results={("psql", "--version"): "psql (PostgreSQL) 16.3"},
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "blocked"
    assert report.compose == "blocked"
    assert report.postgres_client == "available"
    assert report.linux_arch == "x86_64"
    assert "container_cli:blocked:not_found" in report.reasons
    assert "compose:blocked:container_cli_unavailable" in report.reasons


@pytest.mark.parametrize(
    ("executable", "output"),
    [
        ("docker", "hello from not docker 26.1.4"),
        ("podman", "hello from not podman 5.2.1"),
    ],
)
def test_container_cli_rejects_arbitrary_version_output(
    monkeypatch: pytest.MonkeyPatch,
    executable: str,
    output: str,
) -> None:
    _configure(
        monkeypatch,
        available=(executable, "psql"),
        results={
            (executable, "--version"): output,
            ("psql", "--version"): "psql (PostgreSQL) 16.3",
        },
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "blocked"
    assert report.compose == "blocked"
    assert report.postgres_client == "available"
    assert f"container_cli:blocked:unsupported_version:{executable}" in report.reasons
    _assert_sanitized(" ".join(report.reasons))


def test_postgres_client_rejects_arbitrary_version_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(
        monkeypatch,
        results={
            ("docker", "--version"): "Docker version 26.1.4, build test",
            ("docker", "compose", "version"): "Docker Compose version v2.29.1",
            ("psql", "--version"): "hello from not postgres 16.3",
        },
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "available"
    assert report.compose == "available"
    assert report.postgres_client == "blocked"
    assert "postgres_client:blocked:unsupported_version:psql" in report.reasons
    _assert_sanitized(" ".join(report.reasons))


def test_version_output_on_stderr_is_parsed_without_leaking_raw_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(
        monkeypatch,
        results={
            ("docker", "--version"): ("", "Docker version 26.1.4, build test"),
            ("docker", "compose", "version"): ("", "Docker Compose version v2.29.1"),
            ("psql", "--version"): ("", "psql (PostgreSQL) 16.3"),
        },
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "available"
    assert report.compose == "available"
    assert report.postgres_client == "available"
    assert report.reasons == ()


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (
            subprocess.CalledProcessError(
                1,
                ["docker", "--version"],
                stderr="/tmp/docker.sock OPENAI_API_KEY=raw-secret",
            ),
            "container_cli:blocked:version_probe_failed:docker",
        ),
        (
            subprocess.TimeoutExpired(["docker", "--version"], 5),
            "container_cli:blocked:version_probe_timeout:docker",
        ),
    ],
)
def test_container_cli_present_but_version_probe_failure_blocks_safely(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
    reason: str,
) -> None:
    _configure(
        monkeypatch,
        available=("docker", "psql"),
        results={
            ("docker", "--version"): failure,
            ("psql", "--version"): "psql (PostgreSQL) 16.3",
        },
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "blocked"
    assert report.compose == "blocked"
    assert report.postgres_client == "available"
    assert reason in report.reasons
    _assert_sanitized(" ".join(report.reasons))


@pytest.mark.parametrize(
    ("compose_result", "reason"),
    [
        (
            subprocess.CalledProcessError(
                1,
                ["docker", "compose", "version"],
                stderr="/tmp/docker-compose OPENAI_API_KEY=raw-secret",
            ),
            "compose:blocked:version_probe_failed:docker-compose",
        ),
        (
            subprocess.TimeoutExpired(["docker", "compose", "version"], 5),
            "compose:blocked:version_probe_timeout:docker-compose",
        ),
        (
            "docker-compose version 1.29.2, build test",
            "compose:blocked:unsupported_version:docker-compose",
        ),
        (
            "Docker Compose version v1.29.2, build 2",
            "compose:blocked:unsupported_version:docker-compose",
        ),
        (
            "hello from compose build 2",
            "compose:blocked:unsupported_version:docker-compose",
        ),
    ],
)
def test_compose_v1_absent_failure_or_timeout_blocks(
    monkeypatch: pytest.MonkeyPatch,
    compose_result: str | BaseException,
    reason: str,
) -> None:
    _configure(
        monkeypatch,
        available=("docker", "psql"),
        results={
            ("docker", "--version"): "Docker version 26.1.4, build test",
            ("docker", "compose", "version"): compose_result,
            ("psql", "--version"): "psql (PostgreSQL) 16.3",
        },
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "available"
    assert report.compose == "blocked"
    assert report.postgres_client == "available"
    assert reason in report.reasons
    _assert_sanitized(" ".join(report.reasons))


@pytest.mark.parametrize(
    ("available", "psql_result", "reason"),
    [
        (("docker",), None, "postgres_client:blocked:not_found"),
        (
            ("docker", "psql"),
            subprocess.CalledProcessError(
                1,
                ["psql", "--version"],
                stderr="/tmp/psql OPENAI_API_KEY=raw-secret",
            ),
            "postgres_client:blocked:version_probe_failed:psql",
        ),
        (
            ("docker", "psql"),
            subprocess.TimeoutExpired(["psql", "--version"], 5),
            "postgres_client:blocked:version_probe_timeout:psql",
        ),
    ],
)
def test_postgres_client_absent_failure_or_timeout_blocks(
    monkeypatch: pytest.MonkeyPatch,
    available: Sequence[str],
    psql_result: str | BaseException | None,
    reason: str,
) -> None:
    results: dict[tuple[str, ...], ProbeOutcome] = {
        ("docker", "--version"): "Docker version 26.1.4, build test",
        ("docker", "compose", "version"): "Docker Compose version v2.29.1",
    }
    if psql_result is not None:
        results[("psql", "--version")] = psql_result
    _configure(monkeypatch, available=available, results=results)

    report = capabilities.detect_capabilities()

    assert report.container_cli == "available"
    assert report.compose == "available"
    assert report.postgres_client == "blocked"
    assert reason in report.reasons
    _assert_sanitized(" ".join(report.reasons))


@pytest.mark.parametrize(
    "compose_output",
    [
        "Docker Compose version v2.29.1",
        "podman-compose version 2.1.0",
        "Podman Compose version v2.1.0",
    ],
)
def test_compose_v2_output_accepts_only_compose_version_field(
    monkeypatch: pytest.MonkeyPatch,
    compose_output: str,
) -> None:
    _configure(
        monkeypatch,
        available=("podman", "psql"),
        results={
            ("podman", "--version"): "podman version 5.2.1",
            ("podman", "compose", "version"): compose_output,
            ("psql", "--version"): "psql (PostgreSQL) 16.3",
        },
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "available"
    assert report.compose == "available"
    assert report.postgres_client == "available"
    assert report.reasons == ()


def test_docker_compose_failure_continues_to_complete_podman_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure(
        monkeypatch,
        available=("docker", "podman", "psql"),
        results={
            ("docker", "--version"): "Docker version 26.1.4, build test",
            ("docker", "compose", "version"): subprocess.CalledProcessError(
                1,
                ["docker", "compose", "version"],
                stderr="/tmp/docker-compose OPENAI_API_KEY=raw-secret",
            ),
            ("podman", "--version"): "podman version 5.2.1",
            ("podman", "compose", "version"): "podman-compose version 2.1.0",
            ("psql", "--version"): "psql (PostgreSQL) 16.3",
        },
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "available"
    assert report.compose == "available"
    assert report.postgres_client == "available"
    assert report.reasons == ()
    assert [call["args"] for call in harness.calls[:4]] == [
        ("docker", "--version"),
        ("docker", "compose", "version"),
        ("podman", "--version"),
        ("podman", "compose", "version"),
    ]


def test_does_not_mix_valid_docker_cli_with_invalid_podman_cli_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure(
        monkeypatch,
        available=("docker", "podman", "psql"),
        results={
            ("docker", "--version"): "Docker version 26.1.4, build test",
            ("docker", "compose", "version"): subprocess.CalledProcessError(
                1,
                ["docker", "compose", "version"],
            ),
            ("podman", "--version"): "not actually podman 5.2.1",
            ("psql", "--version"): "psql (PostgreSQL) 16.3",
        },
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "available"
    assert report.compose == "blocked"
    assert report.postgres_client == "available"
    assert "compose:blocked:version_probe_failed:docker-compose" in report.reasons
    assert "container_cli:blocked:unsupported_version:podman" in report.reasons
    assert ("podman", "compose", "version") not in [call["args"] for call in harness.calls]


def test_both_candidate_compose_failures_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(
        monkeypatch,
        available=("docker", "podman", "psql"),
        results={
            ("docker", "--version"): "Docker version 26.1.4, build test",
            ("docker", "compose", "version"): "Docker Compose version v1.29.2, build 2",
            ("podman", "--version"): "podman version 5.2.1",
            ("podman", "compose", "version"): subprocess.CalledProcessError(
                1,
                ["podman", "compose", "version"],
                stderr="/tmp/podman-compose OPENAI_API_KEY=raw-secret",
            ),
            ("psql", "--version"): "psql (PostgreSQL) 16.3",
        },
    )

    report = capabilities.detect_capabilities()

    assert report.container_cli == "available"
    assert report.compose == "blocked"
    assert report.postgres_client == "available"
    assert report.reasons == (
        "compose:blocked:unsupported_version:docker-compose",
        "compose:blocked:version_probe_failed:podman-compose",
    )
    _assert_sanitized(" ".join(report.reasons))


def test_require_capabilities_supports_subsets_and_stable_exception_shape() -> None:
    report = capabilities.CapabilityReport(
        container_cli="blocked",
        compose="available",
        postgres_client="blocked",
        linux_arch="x86_64",
        reasons=(
            "container_cli:blocked:not_found",
            "postgres_client:blocked:not_found",
        ),
    )

    capabilities.require_capabilities(report, ("compose",))
    with pytest.raises(capabilities.CapabilityUnavailableError) as exc_info:
        capabilities.require_capabilities(report, ("container_cli", "postgres_client"))

    assert exc_info.value.names == ("container_cli", "postgres_client")
    assert exc_info.value.reasons == (
        "container_cli:blocked:not_found",
        "postgres_client:blocked:not_found",
    )
    assert str(exc_info.value) == "missing required capabilities: container_cli, postgres_client"


def test_require_capabilities_all_path_preserves_order_and_reasons() -> None:
    report = capabilities.CapabilityReport(
        container_cli="blocked",
        compose="blocked",
        postgres_client="blocked",
        linux_arch="blocked",
        reasons=(
            "linux_arch:blocked:unsupported_os",
            "container_cli:blocked:not_found",
            "compose:blocked:container_cli_unavailable",
            "postgres_client:blocked:not_found",
        ),
    )

    with pytest.raises(capabilities.CapabilityUnavailableError) as exc_info:
        capabilities.require_capabilities(report, capabilities.CAPABILITY_NAMES)

    assert exc_info.value.names == (
        "container_cli",
        "compose",
        "postgres_client",
        "linux_arch",
    )
    assert exc_info.value.reasons == (
        "linux_arch:blocked:unsupported_os",
        "container_cli:blocked:not_found",
        "compose:blocked:container_cli_unavailable",
        "postgres_client:blocked:not_found",
    )
    assert str(exc_info.value) == (
        "missing required capabilities: container_cli, compose, postgres_client, linux_arch"
    )


def test_subprocess_probes_use_arrays_check_timeout_and_minimum_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _configure(monkeypatch)

    capabilities.detect_capabilities()

    assert {call["args"] for call in harness.calls} == {
        ("docker", "--version"),
        ("docker", "compose", "version"),
        ("psql", "--version"),
    }
    assert all(call["args_type"] is list for call in harness.calls)
    assert all(call["check"] is True for call in harness.calls)
    assert all(0 < call["timeout"] <= 10 for call in harness.calls)
    assert all(set(call["env"]).issubset(MINIMAL_ENV_KEYS) for call in harness.calls)


def test_cli_diagnostics_are_sanitized_and_requirements_fail_clear(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure(
        monkeypatch,
        available=("docker",),
        results={
            ("docker", "--version"): "Docker version 26.1.4, build test",
            ("docker", "compose", "version"): "Docker Compose version v2.29.1",
        },
    )

    exit_code = capabilities.main(["--require", "postgres_client"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "container_cli=available" in output
    assert "compose=available" in output
    assert "postgres_client=blocked" in output
    assert "missing required capabilities: postgres_client" in output
    _assert_sanitized(output)


def test_import_has_no_subprocess_probe_or_environment_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-stay")

    def fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run called at import time")

    monkeypatch.setattr(subprocess, "run", fail_run)

    reloaded = importlib.reload(capabilities)

    assert reloaded is capabilities
    assert os.environ["OPENAI_API_KEY"] == "must-stay"
