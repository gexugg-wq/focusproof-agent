from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient


_FORBIDDEN_APP_IMPORTS = frozenset(
    {
        "focusproof.openhands_adapter.real_conversation",
    }
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_formal_app_import_does_not_reach_legacy_debug_spike() -> None:
    forbidden = repr(sorted(_FORBIDDEN_APP_IMPORTS))
    probe = f"""
import importlib.abc
import sys

FORBIDDEN = frozenset({forbidden})

class RejectLegacyDebugImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        del path, target
        if fullname in FORBIDDEN:
            raise AssertionError(f"formal app imported legacy debug module: {{fullname}}")
        return None

sys.meta_path.insert(0, RejectLegacyDebugImports())
import focusproof.api.app as app_module
assert not hasattr(app_module, "get_env_status")
assert not hasattr(app_module, "get_llm_config_status")
assert not hasattr(app_module, "real_conversation")
"""
    environ = os.environ.copy()
    environ["PYTHONPATH"] = str(PROJECT_ROOT / "agent-server")

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PROJECT_ROOT,
        env=environ,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("profile", ["local-dev", "staging", "production"])
@pytest.mark.parametrize("debug_flag", [None, "false", "true", "malicious"])
def test_debug_routes_are_absent_for_every_profile_and_flag(
    profile: str,
    debug_flag: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from focusproof.api.app import create_app

    monkeypatch.setenv("FOCUSPROOF_PROFILE", profile)
    if debug_flag is None:
        monkeypatch.delenv("FOCUSPROOF_ENABLE_DEBUG_ROUTES", raising=False)
    else:
        monkeypatch.setenv("FOCUSPROOF_ENABLE_DEBUG_ROUTES", debug_flag)

    application = create_app()
    paths = {getattr(route, "path", None) for route in application.routes}

    assert "/debug/openhands/env-status" not in paths
    assert "/debug/openhands/llm-status" not in paths
    assert "/debug/openhands/conversation-test" not in paths
    client = TestClient(application)
    assert client.get("/debug/openhands/env-status").status_code == 404
    assert client.get("/debug/openhands/llm-status").status_code == 404
    assert client.post(
        "/debug/openhands/conversation-test",
        json={"domain": "general", "goal": "blocked", "evidence": "blocked"},
    ).status_code == 404
