from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
AGENT_SERVER = PROJECT_ROOT / "agent-server"
COST_MAP_KEY = "LITELLM_LOCAL_MODEL_COST_MAP"
IMPORT_TARGETS = (
    "focusproof.api.app",
    "focusproof.openhands_runtime.factory",
    "focusproof.openhands_adapter.real_conversation",
)
INVALID_VALUES = (None, "false", " true ", "malicious-remote-value")


def _subprocess_environment(
    tmp_path: Path,
    *,
    profile: str,
    cost_map_value: str | None,
) -> tuple[dict[str, str], Path]:
    attempts_file = tmp_path / "socket-attempts.txt"
    (tmp_path / "sitecustomize.py").write_text(
        """
import os
from pathlib import Path
import socket

_attempts = Path(os.environ["FOCUSPROOF_SOCKET_ATTEMPTS"])

def _blocked(*args, **kwargs):
    del args, kwargs
    with _attempts.open("a", encoding="utf-8") as stream:
        stream.write("blocked outbound socket\\n")
    raise AssertionError("outbound socket attempted during cold import")

socket.create_connection = _blocked
socket.socket.connect = _blocked
""",
        encoding="utf-8",
    )
    environ = {
        key: value
        for key in ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR")
        if (value := os.environ.get(key)) is not None
    }
    environ.update(
        {
            "PYTHONPATH": os.pathsep.join((str(tmp_path), str(AGENT_SERVER))),
            "FOCUSPROOF_PROFILE": profile,
            "FOCUSPROOF_SOCKET_ATTEMPTS": str(attempts_file),
        }
    )
    if cost_map_value is not None:
        environ[COST_MAP_KEY] = cost_map_value
    return environ, attempts_file


def _run_probe(
    tmp_path: Path,
    *,
    profile: str,
    cost_map_value: str | None,
    source: str,
) -> subprocess.CompletedProcess[str]:
    environ, attempts_file = _subprocess_environment(
        tmp_path,
        profile=profile,
        cost_map_value=cost_map_value,
    )
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=PROJECT_ROOT,
        env=environ,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert not attempts_file.exists(), attempts_file.read_text(encoding="utf-8")
    return completed


@pytest.mark.parametrize("profile", ["local-dev", "deterministic-test", "production"])
@pytest.mark.parametrize("external_value", INVALID_VALUES + ("true",))
def test_benign_focusproof_config_import_preserves_cost_map_environment_exactly(
    tmp_path: Path,
    profile: str,
    external_value: str | None,
) -> None:
    expected = repr(external_value)
    completed = _run_probe(
        tmp_path,
        profile=profile,
        cost_map_value=external_value,
        source=(
            "import os; "
            "import focusproof.config.identity; "
            f"assert os.environ.get('{COST_MAP_KEY}') == {expected}"
        ),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("module_name", IMPORT_TARGETS)
@pytest.mark.parametrize("profile", ["staging", "production"])
@pytest.mark.parametrize("external_value", INVALID_VALUES)
def test_non_local_cold_import_fails_before_openhands_without_mutating_environment(
    tmp_path: Path,
    module_name: str,
    profile: str,
    external_value: str | None,
) -> None:
    expected = repr(external_value)
    completed = _run_probe(
        tmp_path,
        profile=profile,
        cost_map_value=external_value,
        source=f"""
import importlib
import os
import sys

try:
    importlib.import_module({module_name!r})
except Exception as exc:
    assert type(exc).__name__ == "CostMapPreflightError", type(exc).__name__
    assert str(exc) == "local model cost map is required"
    assert os.environ.get({COST_MAP_KEY!r}) == {expected}
    assert not any(
        name == "openhands" or name.startswith("openhands.")
        for name in sys.modules
    )
    assert not any(
        name == "litellm" or name.startswith("litellm.")
        for name in sys.modules
    )
else:
    raise AssertionError("unsafe OpenHands import unexpectedly succeeded")
""",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("module_name", IMPORT_TARGETS)
@pytest.mark.parametrize("profile", ["staging", "production"])
def test_non_local_cold_import_accepts_only_explicit_true_without_network(
    tmp_path: Path,
    module_name: str,
    profile: str,
) -> None:
    completed = _run_probe(
        tmp_path,
        profile=profile,
        cost_map_value="true",
        source=(
            f"import {module_name}; import os; "
            f"assert os.environ[{COST_MAP_KEY!r}] == 'true'"
        ),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("module_name", IMPORT_TARGETS)
@pytest.mark.parametrize("profile", ["local-dev", "deterministic-test"])
@pytest.mark.parametrize("external_value", INVALID_VALUES)
def test_local_and_test_cold_import_force_bundled_map_only_at_openhands_boundary(
    tmp_path: Path,
    module_name: str,
    profile: str,
    external_value: str | None,
) -> None:
    completed = _run_probe(
        tmp_path,
        profile=profile,
        cost_map_value=external_value,
        source=(
            f"import {module_name}; import os; "
            f"assert os.environ[{COST_MAP_KEY!r}] == 'true'"
        ),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("module_name", IMPORT_TARGETS)
def test_unknown_profile_fails_closed_before_openhands(
    tmp_path: Path,
    module_name: str,
) -> None:
    completed = _run_probe(
        tmp_path,
        profile="unknown-profile",
        cost_map_value="true",
        source=f"""
import importlib
import os
import sys

try:
    importlib.import_module({module_name!r})
except Exception as exc:
    assert type(exc).__name__ == "CostMapPreflightError"
    assert str(exc) == "runtime profile is invalid"
    assert os.environ[{COST_MAP_KEY!r}] == "true"
    assert not any(
        name == "openhands" or name.startswith("openhands.")
        for name in sys.modules
    )
else:
    raise AssertionError("unknown profile unexpectedly imported OpenHands")
""",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
