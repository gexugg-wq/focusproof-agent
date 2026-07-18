from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
AGENT_SERVER = PROJECT_ROOT / "agent-server"
IMPORT_TARGETS = (
    "focusproof.api.app",
    "focusproof.openhands_runtime.factory",
    "focusproof.openhands_adapter.real_conversation",
)


@pytest.mark.parametrize("module_name", IMPORT_TARGETS)
@pytest.mark.parametrize("external_value", [None, "false", "malicious-remote-value"])
def test_cold_focusproof_import_forces_bundled_cost_map_without_network(
    tmp_path: Path,
    module_name: str,
    external_value: str | None,
) -> None:
    attempts_file = tmp_path / "socket-attempts.txt"
    sitecustomize = tmp_path / "sitecustomize.py"
    sitecustomize.write_text(
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
    environ = os.environ.copy()
    environ["PYTHONPATH"] = os.pathsep.join((str(tmp_path), str(AGENT_SERVER)))
    environ["FOCUSPROOF_SOCKET_ATTEMPTS"] = str(attempts_file)
    environ.pop("DASHSCOPE_API_KEY", None)
    environ.pop("OPENAI_API_KEY", None)
    environ.pop("ANTHROPIC_API_KEY", None)
    environ.pop("FOCUSPROOF_LLM_API_KEY", None)
    if external_value is None:
        environ.pop("LITELLM_LOCAL_MODEL_COST_MAP", None)
    else:
        environ["LITELLM_LOCAL_MODEL_COST_MAP"] = external_value

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"import {module_name}; import os; "
                "assert os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] == 'true'"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environ,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not attempts_file.exists(), attempts_file.read_text(encoding="utf-8")
