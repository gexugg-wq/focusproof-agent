from __future__ import annotations

import io
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import run_quality_gate as gate


def test_tier_inheritance_appends_integration_and_release_steps() -> None:
    fast = gate.resolve_steps("fast")
    integration = gate.resolve_steps("integration")
    release = gate.resolve_steps("release")

    fast_names = [step.name for step in fast]
    integration_names = [step.name for step in integration]
    release_names = [step.name for step in release]

    assert integration_names[: len(fast_names)] == fast_names
    assert release_names[: len(integration_names)] == integration_names
    assert any(step.uses_real_provider for step in release)


def test_fast_tier_strips_provider_keys_and_keeps_real_networked_steps_out() -> None:
    source = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "secret-openai",
        "DASHSCOPE_API_KEY": "secret-dashscope",
        "FOCUSPROOF_LLM_API_KEY": "secret-focusproof",
    }

    child = gate.build_child_env(source=source)
    fast = gate.resolve_steps("fast")

    assert child == {"PATH": "/usr/bin", "LITELLM_LOCAL_MODEL_COST_MAP": "true"}
    assert all(not step.uses_real_provider for step in fast)
    assert all(not step.requires_docker for step in fast)
    assert all(not step.uses_network for step in fast)


def test_static_gates_exclude_full_tree_format_blocker() -> None:
    for tier in ("fast", "integration", "release"):
        names = {step.name for step in gate.resolve_steps(tier)}

        assert "ruff-check" in names
        assert "mypy-strict" in names
        assert "ruff-format-check" not in names


def test_release_without_explicit_real_provider_authorization_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        gate,
        "run_steps",
        lambda *args, **kwargs: pytest.fail("release gate must stop before executing steps"),
    )

    exit_code = gate.main(["--tier", "release"])

    assert exit_code == 2
    assert "--allow-real-provider" in capsys.readouterr().out


def test_list_prints_resolved_steps_without_execution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        gate,
        "run_steps",
        lambda *args, **kwargs: pytest.fail("list mode must not execute steps"),
    )

    exit_code = gate.main(["--tier", "fast", "--list"])

    assert exit_code == 0
    rendered = capsys.readouterr().out
    assert "tier=fast" in rendered
    assert "step=backend-visual-contracts" in rendered
    assert "Deterministic visual runner and product-path contracts." in rendered


def test_release_dry_run_prints_commands_without_execution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "scripts.run_quality_gate.subprocess.run",
        lambda *args, **kwargs: pytest.fail("dry-run must not execute subprocesses"),
    )

    exit_code = gate.main(["--tier", "release", "--dry-run"])

    assert exit_code == 0
    rendered = capsys.readouterr().out
    assert "step=release-live-clamd duration=0.00s exit=0" in rendered
    assert "step=release-real-vision duration=0.00s exit=0" in rendered
    assert "{release_dir}" not in rendered
    assert ".local/quality-gates/release/live-clamd.json" in rendered
    assert "agent-server/tests/fixtures/real-vision/focusproof-general-session.png" in rendered
    assert "docs/research/assets/ai5/task7/chromium-success.png" not in rendered
    assert "summary tier=release status=PASS" in rendered


def test_run_steps_stops_after_first_failure_and_reports_summary() -> None:
    steps = (
        gate.StepSpec(name="first", command=("python", "-m", "pytest"), summary="first step"),
        gate.StepSpec(name="second", command=("python", "-m", "ruff"), summary="second step"),
        gate.StepSpec(name="third", command=("python", "-m", "mypy"), summary="third step"),
    )
    output = io.StringIO()
    calls: list[str] = []

    def fake_runner(step: gate.StepSpec, env: dict[str, str]) -> int:
        assert "OPENAI_API_KEY" not in env
        calls.append(step.name)
        return 0 if step.name == "first" else 1

    summary = gate.run_steps(
        "integration",
        steps,
        output=output,
        runner=fake_runner,
        clock=iter((10.0, 11.0, 20.0, 21.5, 30.0)).__next__,
    )

    assert summary.exit_code == 1
    assert summary.status == "FAIL"
    assert calls == ["first", "second"]
    rendered = output.getvalue()
    assert "step=first duration=1.00s exit=0" in rendered
    assert "step=second duration=1.50s exit=1" in rendered
    assert "step=third" not in rendered
    assert (
        "summary tier=integration status=FAIL total=3 completed=2 passed=1 failed=1 blocked=0 not_run=1"
        in rendered
    )


def test_unknown_tier_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown tier: unknown"):
        gate.resolve_steps("unknown")


def test_quality_gate_script_entrypoint_runs_dry_run() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "run_quality_gate.py"),
            "--tier",
            "fast",
            "--dry-run",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "step=backend-visual-contracts duration=0.00s exit=0" in completed.stdout
    assert "summary tier=fast status=PASS" in completed.stdout
