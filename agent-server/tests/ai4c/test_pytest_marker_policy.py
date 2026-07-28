from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[3]
STAGING_FILE = "agent-server/tests/ai4c/test_staging_stack.py"
EXTERNAL_TEST = "test_staging_external_stack_builds_runs_and_preserves_ids"
EXTERNAL_NODE = f"{STAGING_FILE}::{EXTERNAL_TEST}"


def _collect(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in (
        "DASHSCOPE_API_KEY",
        "OPENAI_API_KEY",
        "FOCUSPROOF_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        environment.pop(name, None)
    environment["LITELLM_LOCAL_MODEL_COST_MAP"] = "true"
    return subprocess.run(
        [sys.executable, "-m", "pytest", *arguments, "--collect-only", "-q"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_default_pytest_policy_excludes_external_markers_from_ordinary_suite() -> None:
    result = _collect("agent-server/tests/ai4c")

    assert result.returncode == 0, result.stderr
    assert EXTERNAL_NODE not in result.stdout
    assert "test_real_provider.py::test_dashscope_smoke_uses_native_bounded_conversation" not in result.stdout
    assert "test_postgres_persistence.py::" not in result.stdout


def test_default_pytest_policy_excludes_external_test_from_whole_file() -> None:
    result = _collect(STAGING_FILE)

    assert result.returncode == 0, result.stderr
    assert EXTERNAL_NODE not in result.stdout
    assert "1 deselected" in result.stdout


def test_focused_external_node_without_marker_authorization_is_not_selected() -> None:
    result = _collect(EXTERNAL_NODE)

    assert result.returncode == 5
    assert EXTERNAL_NODE not in result.stdout
    assert "1 deselected" in result.stdout


def test_explicit_staging_external_marker_overrides_default_for_collection_only() -> None:
    result = _collect(STAGING_FILE, "-m", "staging_external")

    assert result.returncode == 0, result.stderr
    assert EXTERNAL_NODE in result.stdout
    assert "1/75 tests collected" in result.stdout


def test_marker_policy_uses_pytest_configuration_without_collection_hooks() -> None:
    configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = configuration["tool"]["pytest"]["ini_options"]["addopts"]

    assert "-m" in addopts
    for marker in ("real_llm", "postgres", "staging_external"):
        assert f"not {marker}" in addopts

    conftests = (PROJECT_ROOT / "agent-server" / "tests").rglob("conftest.py")
    assert all(
        "pytest_collection_modifyitems" not in path.read_text(encoding="utf-8")
        for path in conftests
    )
