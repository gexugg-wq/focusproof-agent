from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import os
import platform
import re
import shutil
import subprocess
import sys
from typing import Literal


CapabilityState = Literal["available", "blocked"]
CapabilityName = Literal["container_cli", "compose", "postgres_client", "linux_arch"]

PROBE_TIMEOUT_SECONDS = 5.0
PROVIDER_KEYS = frozenset(
    (
        "DASHSCOPE_API_KEY",
        "OPENAI_API_KEY",
        "FOCUSPROOF_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
    )
)
MINIMAL_ENV_KEYS = ("PATH", "LANG", "LC_ALL")
CAPABILITY_NAMES: tuple[CapabilityName, ...] = (
    "container_cli",
    "compose",
    "postgres_client",
    "linux_arch",
)


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    container_cli: CapabilityState
    compose: CapabilityState
    postgres_client: CapabilityState
    linux_arch: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Probe:
    state: CapabilityState
    command: str | None
    reason: str | None


class CapabilityUnavailableError(RuntimeError):
    names: tuple[str, ...]
    reasons: tuple[str, ...]

    def __init__(self, names: Sequence[str], reasons: Sequence[str]) -> None:
        self.names = tuple(names)
        self.reasons = tuple(reasons)
        super().__init__(f"missing required capabilities: {', '.join(self.names)}")


def _minimal_environment() -> dict[str, str]:
    return {
        key: value
        for key in MINIMAL_ENV_KEYS
        if (value := os.environ.get(key)) is not None and key not in PROVIDER_KEYS
    }


def _run_probe(args: Sequence[str], *, reason_tool: str, capability: str) -> _Probe:
    try:
        completed = subprocess.run(
            list(args),
            check=True,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            env=_minimal_environment(),
        )
    except subprocess.TimeoutExpired:
        return _Probe(
            state="blocked",
            command=reason_tool,
            reason=f"{capability}:blocked:version_probe_timeout:{reason_tool}",
        )
    except (OSError, subprocess.CalledProcessError):
        return _Probe(
            state="blocked",
            command=reason_tool,
            reason=f"{capability}:blocked:version_probe_failed:{reason_tool}",
        )
    if completed.stdout.strip():
        return _Probe(state="available", command=reason_tool, reason=None)
    return _Probe(
        state="blocked",
        command=reason_tool,
        reason=f"{capability}:blocked:unsupported_version:{reason_tool}",
    )


def _detect_container_cli() -> _Probe:
    candidate_reasons: list[str] = []
    for executable in ("docker", "podman"):
        if shutil.which(executable) is None:
            continue
        probe = _run_probe(
            [executable, "--version"],
            reason_tool=executable,
            capability="container_cli",
        )
        if probe.state == "available":
            return probe
        if probe.reason is not None:
            candidate_reasons.append(probe.reason)
    if candidate_reasons:
        return _Probe(state="blocked", command=None, reason=candidate_reasons[0])
    return _Probe(state="blocked", command=None, reason="container_cli:blocked:not_found")


def _compose_is_v2(output: str) -> bool:
    for token in re.split(r"[\s,()]+", output):
        version = token.lower().removeprefix("v")
        if re.match(r"^2(?:\.|$)", version) is not None:
            return True
    return False


def _detect_compose(container: _Probe) -> _Probe:
    if container.state != "available" or container.command is None:
        return _Probe(
            state="blocked",
            command=None,
            reason="compose:blocked:container_cli_unavailable",
        )
    reason_tool = f"{container.command}-compose"
    try:
        completed = subprocess.run(
            [container.command, "compose", "version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            env=_minimal_environment(),
        )
    except subprocess.TimeoutExpired:
        return _Probe(
            state="blocked",
            command=reason_tool,
            reason=f"compose:blocked:version_probe_timeout:{reason_tool}",
        )
    except (OSError, subprocess.CalledProcessError):
        return _Probe(
            state="blocked",
            command=reason_tool,
            reason=f"compose:blocked:version_probe_failed:{reason_tool}",
        )
    if _compose_is_v2(completed.stdout):
        return _Probe(state="available", command=reason_tool, reason=None)
    return _Probe(
        state="blocked",
        command=reason_tool,
        reason=f"compose:blocked:unsupported_version:{reason_tool}",
    )


def _detect_postgres_client() -> _Probe:
    if shutil.which("psql") is None:
        return _Probe(state="blocked", command=None, reason="postgres_client:blocked:not_found")
    return _run_probe(
        ["psql", "--version"],
        reason_tool="psql",
        capability="postgres_client",
    )


def detect_capabilities() -> CapabilityReport:
    if platform.system() != "Linux":
        return CapabilityReport(
            container_cli="blocked",
            compose="blocked",
            postgres_client="blocked",
            linux_arch="blocked",
            reasons=("linux_arch:blocked:unsupported_os",),
        )

    container = _detect_container_cli()
    compose = _detect_compose(container)
    postgres_client = _detect_postgres_client()
    reasons = tuple(
        reason
        for reason in (container.reason, compose.reason, postgres_client.reason)
        if reason is not None
    )
    return CapabilityReport(
        container_cli=container.state,
        compose=compose.state,
        postgres_client=postgres_client.state,
        linux_arch=platform.machine() or "unknown",
        reasons=reasons,
    )


def _capability_state(report: CapabilityReport, name: str) -> CapabilityState:
    if name == "container_cli":
        return report.container_cli
    if name == "compose":
        return report.compose
    if name == "postgres_client":
        return report.postgres_client
    if name == "linux_arch":
        return "available" if report.linux_arch != "blocked" else "blocked"
    raise ValueError(f"unknown capability: {name}")


def _matching_reasons(report: CapabilityReport, missing: Sequence[str]) -> tuple[str, ...]:
    prefixes = tuple(f"{name}:" for name in missing)
    matched = tuple(reason for reason in report.reasons if reason.startswith(prefixes))
    if matched:
        return matched
    return report.reasons


def require_capabilities(report: CapabilityReport, names: Sequence[str]) -> None:
    missing = tuple(name for name in names if _capability_state(report, name) == "blocked")
    if missing:
        raise CapabilityUnavailableError(missing, _matching_reasons(report, missing))


def _diagnostic_lines(report: CapabilityReport) -> list[str]:
    lines = [
        f"container_cli={report.container_cli}",
        f"compose={report.compose}",
        f"postgres_client={report.postgres_client}",
        f"linux_arch={report.linux_arch}",
    ]
    if report.reasons:
        lines.append("reasons=" + ",".join(report.reasons))
    return lines


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check FocusProof AI4C staging host capabilities.",
    )
    parser.add_argument(
        "--require",
        nargs="+",
        choices=CAPABILITY_NAMES,
        default=(),
        help="Capability names that must be available.",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    report = detect_capabilities()
    for line in _diagnostic_lines(report):
        print(line)
    try:
        require_capabilities(report, tuple(args.require))
    except CapabilityUnavailableError as exc:
        print(str(exc))
        if exc.reasons:
            print("blockers=" + ",".join(exc.reasons))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
