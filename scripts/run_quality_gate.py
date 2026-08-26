from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Literal, TextIO

ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / ".local" / "quality-gates" / "release"
CANONICAL_IMAGE = (
    ROOT / "agent-server" / "tests" / "fixtures" / "real-vision" / "focusproof-general-session.png"
)
FAST_MARKERS = (
    "not real_llm and not staging_external and not external and not postgres and not postgres_media"
)
PROVIDER_KEY_NAMES = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AZURE_OPENAI_API_KEY",
        "DASHSCOPE_API_KEY",
        "FOCUSPROOF_LLM_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "LLM_API_KEY",
        "OPENAI_API_KEY",
    }
)
TierName = Literal["fast", "integration", "release"]
RunStatus = Literal["PASS", "FAIL", "BLOCKED"]


@dataclass(frozen=True, slots=True)
class StepSpec:
    name: str
    command: tuple[str, ...]
    summary: str
    uses_network: bool = False
    uses_real_provider: bool = False
    requires_docker: bool = False
    required_env: tuple[str, ...] = ()
    required_any_env: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class RunSummary:
    tier: TierName
    status: RunStatus
    total: int
    completed: int
    passed: int
    failed: int
    blocked: int
    not_run: int
    duration_seconds: float
    exit_code: int


Runner = Callable[[StepSpec, dict[str, str]], int]

_TIER_EXTENDS: dict[TierName, TierName | None] = {
    "fast": None,
    "integration": "fast",
    "release": "integration",
}

_TIER_STEPS: dict[TierName, tuple[StepSpec, ...]] = {
    "fast": (
        StepSpec(
            name="backend-visual-contracts",
            command=(
                "{python}",
                "-m",
                "pytest",
                "-q",
                "agent-server/tests/ai5/test_real_visual_provider_gate_contract.py",
                "agent-server/tests/ai5/test_real_visual_provider_product_path.py",
            ),
            summary="Deterministic visual runner and product-path contracts.",
        ),
        StepSpec(
            name="backend-media-security",
            command=(
                "{python}",
                "-m",
                "pytest",
                "-q",
                "agent-server/tests/ai5/test_real_media_gate_contract.py",
                "agent-server/tests/media_adapters/test_clamd_malware_scanner.py",
                "agent-server/tests/media_core/test_malware_scanner_contract.py",
                "agent-server/tests/integration/test_media_malware_admission.py",
                "agent-server/tests/media_adapters/test_media_composition.py",
                "agent-server/tests/media_adapters/test_media_security_policy.py",
                "agent-server/tests/architecture/test_media_import_boundaries.py",
                "-m",
                FAST_MARKERS,
            ),
            summary="Deterministic media, malware, and import-boundary coverage.",
        ),
        StepSpec(
            name="backend-openhands-runtime",
            command=(
                "{python}",
                "-m",
                "pytest",
                "-q",
                "agent-server/tests/openhands_adapter",
                "agent-server/tests/openhands_runtime",
                "agent-server/tests/integration/test_image_review.py",
                "agent-server/tests/domain/test_media_scoring.py",
                "-m",
                FAST_MARKERS,
            ),
            summary="Official OpenHands adapter/runtime and image review regression.",
        ),
        StepSpec(
            name="backend-gate-contracts",
            command=(
                "{python}",
                "-m",
                "pytest",
                "-q",
                "agent-server/tests/ai4b/test_release_artifacts.py",
                "agent-server/tests/ai4c/test_capability_preflight.py",
                "agent-server/tests/ai4c/test_general_core_gate.py",
                "agent-server/tests/ai4c/test_pytest_marker_policy.py",
                "agent-server/tests/ai5/test_quality_gate.py",
            ),
            summary="Gate, artifact, marker-policy, and capability contracts.",
        ),
        StepSpec(
            name="ruff-check",
            command=("{python}", "-m", "ruff", "check", "scripts", "agent-server"),
            summary="Python lint for scripts and backend.",
        ),
        StepSpec(
            name="mypy-strict",
            command=(
                "{python}",
                "-m",
                "mypy",
                "--strict",
                "scripts/run_quality_gate.py",
                "agent-server",
            ),
            summary="Strict backend and quality-gate typing.",
        ),
        StepSpec(
            name="frontend-typecheck",
            command=("npm", "--prefix", "frontend", "run", "typecheck"),
            summary="Frontend TypeScript type safety.",
        ),
        StepSpec(
            name="frontend-unit-tests",
            command=("npm", "--prefix", "frontend", "run", "test"),
            summary="Frontend deterministic unit tests.",
        ),
    ),
    "integration": (
        StepSpec(
            name="integration-capabilities",
            command=(
                "{python}",
                "scripts/check_ai4c_capabilities.py",
                "--require",
                "linux_arch",
                "postgres_client",
            ),
            summary="Fail closed when the integration host lacks Linux/PostgreSQL prerequisites.",
        ),
        StepSpec(
            name="integration-backup-restore",
            command=(
                "{python}",
                "-m",
                "pytest",
                "-q",
                "agent-server/tests/ai4c/test_backup_restore.py",
            ),
            summary="Deterministic backup/restore coverage without real LLMs.",
        ),
        StepSpec(
            name="integration-postgres-concurrency",
            command=(
                "{python}",
                "-m",
                "pytest",
                "-q",
                "agent-server/tests/persistence/test_media_postgres_concurrency.py",
                "-m",
                "postgres_media",
            ),
            summary="Disposable PostgreSQL media concurrency gate.",
            required_env=("FOCUSPROOF_TEST_POSTGRES_MEDIA_URL",),
        ),
        StepSpec(
            name="frontend-build",
            command=("npm", "--prefix", "frontend", "run", "build"),
            summary="Production frontend build integrity.",
        ),
        StepSpec(
            name="frontend-e2e",
            command=("npm", "--prefix", "frontend", "run", "test:e2e"),
            summary="Deterministic FastAPI and frontend end-to-end flows.",
        ),
    ),
    "release": (
        StepSpec(
            name="release-live-clamd",
            command=(
                "{python}",
                "scripts/run_real_image_evidence_gate.py",
                "--report",
                "{release_dir}/live-clamd.json",
                "--clamd-endpoint",
                "{env:FOCUSPROOF_CLAMD_ENDPOINT}",
            ),
            summary="Live five-case Clamd matrix.",
            uses_network=True,
            required_env=("FOCUSPROOF_CLAMD_ENDPOINT",),
        ),
        StepSpec(
            name="release-real-vision",
            command=(
                "{python}",
                "scripts/run_real_visual_provider_gate.py",
                "--execute-real-provider",
                "--report",
                "{release_dir}/real-vision.json",
                "--image",
                "{canonical_image}",
                "--provider",
                "openai",
                "--model",
                "qwen3.7-plus",
                "--scanner-mode",
                "fake-clean",
            ),
            summary="Explicit real Qwen/OpenHands visual-provider gate.",
            uses_network=True,
            uses_real_provider=True,
            required_any_env=(
                ("DASHSCOPE_API_KEY", "OPENAI_API_KEY"),
                ("DASHSCOPE_BASE_URL", "OPENAI_BASE_URL"),
            ),
        ),
        StepSpec(
            name="release-final-dual-manifest",
            command=(
                "{python}",
                "scripts/build_final_dual_mode_manifest.py",
                "--visual-report",
                "{release_dir}/real-vision.json",
                "--live-clamd-report",
                "{release_dir}/live-clamd.json",
                "--output",
                "{release_dir}/final-dual-mode.json",
            ),
            summary="Dual-mode release manifest synthesis.",
        ),
    ),
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run tiered FocusProof quality gates.")
    parser.add_argument("--tier", choices=tuple(_TIER_EXTENDS), default="fast")
    parser.add_argument("--allow-real-provider", action="store_true")
    parser.add_argument(
        "--list", action="store_true", help="List the resolved steps for the selected tier."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print commands without executing them."
    )
    return parser.parse_args(argv)


def resolve_steps(tier: str) -> tuple[StepSpec, ...]:
    if tier not in _TIER_EXTENDS:
        raise ValueError(f"unknown tier: {tier}")
    parent = _TIER_EXTENDS[tier]
    inherited = () if parent is None else resolve_steps(parent)
    return inherited + _TIER_STEPS[tier]


def build_child_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    child = dict(os.environ if source is None else source)
    for name in PROVIDER_KEY_NAMES:
        child.pop(name, None)
    child["LITELLM_LOCAL_MODEL_COST_MAP"] = "true"
    return child


def _step_env(
    step: StepSpec, source: Mapping[str, str], *, allow_real_provider: bool
) -> dict[str, str]:
    if step.uses_real_provider and allow_real_provider:
        child = dict(source)
        child.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
        return child
    return build_child_env(source=source)


def _render_command(step: StepSpec, source: Mapping[str, str]) -> tuple[str, ...]:
    tokens: list[str] = []
    replacements = {
        "{python}": sys.executable,
        "{release_dir}": str(RELEASE_DIR),
        "{canonical_image}": str(CANONICAL_IMAGE),
    }
    for token in step.command:
        if token.startswith("{env:") and token.endswith("}"):
            tokens.append(source.get(token[5:-1], ""))
        else:
            rendered = token
            for placeholder, value in replacements.items():
                rendered = rendered.replace(placeholder, value)
            tokens.append(rendered)
    return tuple(tokens)


def _missing_prerequisites(step: StepSpec, source: Mapping[str, str]) -> list[str]:
    missing = [name for name in step.required_env if not source.get(name)]
    for group in step.required_any_env:
        if not any(source.get(name) for name in group):
            missing.append("one_of:" + ",".join(group))
    return missing


def _run_command(step: StepSpec, env: dict[str, str]) -> int:
    command = _render_command(step, env)
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    return completed.returncode


def _dry_run_command(step: StepSpec, env: dict[str, str]) -> int:
    _ = step, env
    return 0


def _print_summary(summary: RunSummary, output: TextIO) -> None:
    print(
        "summary "
        f"tier={summary.tier} status={summary.status} total={summary.total} "
        f"completed={summary.completed} passed={summary.passed} failed={summary.failed} "
        f"blocked={summary.blocked} not_run={summary.not_run} duration={summary.duration_seconds:.2f}s "
        f"exit={summary.exit_code}",
        file=output,
    )


def run_steps(
    tier: str,
    steps: Sequence[StepSpec],
    *,
    output: TextIO,
    runner: Runner = _run_command,
    clock: Callable[[], float] = time.monotonic,
    source_env: Mapping[str, str] | None = None,
    allow_real_provider: bool = False,
    check_prerequisites: bool = True,
) -> RunSummary:
    if tier not in _TIER_EXTENDS:
        raise ValueError(f"unknown tier: {tier}")
    source = dict(os.environ if source_env is None else source_env)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    passed = 0
    started_all = clock()
    for index, step in enumerate(steps, start=1):
        missing = _missing_prerequisites(step, source)
        env = _step_env(step, source, allow_real_provider=allow_real_provider)
        command = _render_command(step, env)
        if missing and check_prerequisites:
            finished_all = started_all if index == 1 else clock()
            print(
                f"step={step.name} duration=0.00s exit=2 command={shlex.join(command)} "
                f"reason=missing:{','.join(missing)}",
                file=output,
            )
            summary = RunSummary(
                tier=tier,
                status="BLOCKED",
                total=len(steps),
                completed=index,
                passed=passed,
                failed=0,
                blocked=1,
                not_run=len(steps) - index,
                duration_seconds=finished_all - started_all,
                exit_code=2,
            )
            _print_summary(summary, output)
            return summary
        started = started_all if index == 1 else clock()
        exit_code = runner(step, env)
        finished_all = clock()
        duration = finished_all - started
        print(
            f"step={step.name} duration={duration:.2f}s exit={exit_code} command={shlex.join(command)}",
            file=output,
        )
        if exit_code == 0:
            passed += 1
            continue
        summary = RunSummary(
            tier=tier,
            status="FAIL",
            total=len(steps),
            completed=index,
            passed=passed,
            failed=1,
            blocked=0,
            not_run=len(steps) - index,
            duration_seconds=finished_all - started_all,
            exit_code=exit_code,
        )
        _print_summary(summary, output)
        return summary
    finished_all = clock()
    summary = RunSummary(
        tier=tier,
        status="PASS",
        total=len(steps),
        completed=len(steps),
        passed=passed,
        failed=0,
        blocked=0,
        not_run=0,
        duration_seconds=finished_all - started_all,
        exit_code=0,
    )
    _print_summary(summary, output)
    return summary


def _print_listing(
    tier: TierName, steps: Sequence[StepSpec], output: TextIO, source: Mapping[str, str]
) -> None:
    print(f"tier={tier}", file=output)
    for step in steps:
        command = _render_command(step, source)
        print(
            f"step={step.name} summary={step.summary} command={shlex.join(command)}",
            file=output,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    tier = args.tier
    source = dict(os.environ)
    steps = resolve_steps(tier)

    if args.list:
        _print_listing(tier, steps, sys.stdout, source)
        return 0

    if tier == "release" and not args.allow_real_provider and not args.dry_run:
        print(
            "release tier requires explicit --allow-real-provider authorization",
            file=sys.stdout,
        )
        return 2

    runner: Runner = _run_command
    check_prerequisites = True
    if args.dry_run:
        runner = _dry_run_command
        check_prerequisites = False

    summary = run_steps(
        tier,
        steps,
        output=sys.stdout,
        runner=runner,
        source_env=source,
        allow_real_provider=args.allow_real_provider,
        check_prerequisites=check_prerequisites,
    )
    return summary.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
