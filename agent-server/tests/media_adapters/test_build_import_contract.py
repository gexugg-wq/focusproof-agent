from __future__ import annotations

import ast
import re
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_media_extra_has_exact_bounded_dependencies() -> None:
    import tomllib

    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["optional-dependencies"]["media"] == [
        "Pillow>=12.1.1,<13",
        "python-multipart>=0.0.20,<0.1",
    ]


def test_single_production_lock_is_hash_locked_for_media_dependencies() -> None:
    locks = list((PROJECT_ROOT / "requirements").glob("*production*.lock"))
    assert locks == [PROJECT_ROOT / "requirements" / "production.lock"]
    text = locks[0].read_text(encoding="utf-8")
    requirement_starts = tuple(re.finditer(r"(?m)^[a-z0-9][a-z0-9_.-]*==", text))
    for requirement in ("pillow==", "python-multipart=="):
        start = text.index(requirement)
        end = next(
            (match.start() for match in requirement_starts if match.start() > start),
            len(text),
        )
        block = text[start:end]
        assert "--hash=sha256:" in block


def test_docker_core_and_media_targets_share_the_single_lock() -> None:
    dockerfile = (PROJECT_ROOT / "deploy" / "agent-server.Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "AS runtime" in dockerfile
    assert "AS core" in dockerfile
    assert "AS media" in dockerfile
    assert "AS final" in dockerfile
    assert dockerfile.count("pip install") == 1
    assert dockerfile.count("source=requirements/production.lock") == 1
    assert "FROM runtime AS core" in dockerfile
    assert "FROM runtime AS media" in dockerfile
    assert "FROM core AS final" in dockerfile
    assert "FOCUSPROOF_MEDIA_ENABLED=false" in dockerfile
    assert "FOCUSPROOF_MEDIA_ENABLED=true" in dockerfile
    assert "production-core" not in dockerfile
    assert "production-media" not in dockerfile


def test_staging_compose_selects_media_target_and_feature_configuration() -> None:
    import yaml

    compose = yaml.safe_load(
        (PROJECT_ROOT / "deploy" / "compose.staging.yml").read_text(encoding="utf-8")
    )
    service = compose["services"]["agent-server"]
    assert service["build"]["target"] == "media"
    assert service["environment"]["FOCUSPROOF_MEDIA_ENABLED"] == "true"


def test_disabled_cold_import_does_not_import_focusproof_media_adapters() -> None:
    source = """
import sys
import focusproof.api.app
assert not any(name == 'focusproof.media_adapters' or name.startswith('focusproof.media_adapters.') for name in sys.modules)
"""
    env = {
        "PATH": "",
        "PYTHONPATH": str(PROJECT_ROOT / "agent-server"),
        "FOCUSPROOF_PROFILE": "deterministic-test",
        "FOCUSPROOF_MEDIA_ENABLED": "false",
    }
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_task3_does_not_add_api_upload_routes() -> None:
    tree = ast.parse(
        (PROJECT_ROOT / "agent-server/focusproof/api/app.py").read_text(encoding="utf-8")
    )
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "image" in node.name
        for node in ast.walk(tree)
    )
