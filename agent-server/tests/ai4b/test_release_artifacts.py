from __future__ import annotations

import ast
import io
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
REQUIRED_ARTIFACTS = (
    Path("docs/security/THREAT_MODEL.md"),
    Path("docs/security/SECURITY_ACCEPTANCE.md"),
    Path("docs/deployment/LOCAL_WSL.md"),
    Path("docs/deployment/STAGING.md"),
    Path("docs/deployment/OPERATIONS.md"),
    Path("scripts/run_ai4b_test_server.py"),
    Path("scripts/ai4b_smoke.py"),
    Path("scripts/ai4b_check.py"),
)
TEXT_SCAN_ROOTS = (
    Path("docs"),
    Path("scripts"),
    Path("agent-server/tests"),
    Path("frontend/e2e"),
)
FAKE_SECRET_SENTINEL = "sk-" + "ai4b-not-a-real-secret"
FAKE_SECRET_ALLOWLIST = {
    Path("agent-server/tests/ai4b/test_api_security.py"),
}
PROVIDER_KEYS = {
    "DASHSCOPE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "LLM_API_KEY",
}


def _artifact(path: str) -> Path:
    return ROOT / path


def _load_script(name: str) -> Any:
    import importlib.util

    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tracked_text_paths() -> set[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--", *(str(path) for path in TEXT_SCAN_ROOTS)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = {
        Path(line) for line in completed.stdout.splitlines() if line and "__pycache__" not in line
    }
    paths.update(REQUIRED_ARTIFACTS)
    paths.add(Path("agent-server/tests/ai4b/test_release_artifacts.py"))
    return {
        path
        for path in paths
        if (ROOT / path).is_file()
        and (ROOT / path).suffix.lower()
        in {
            ".example",
            ".ini",
            ".js",
            ".json",
            ".md",
            ".py",
            ".sh",
            ".toml",
            ".ts",
            ".tsx",
            ".txt",
            ".yaml",
            ".yml",
        }
    }


def _provider_assignment_value(line: str, key_name: str) -> str | None:
    stripped = line.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        value = parsed.get(key_name)
        return value if isinstance(value, str) else None

    if stripped.startswith("export "):
        stripped = stripped.removeprefix("export ").lstrip()
    stripped = stripped.lstrip("{").rstrip("}").strip()
    for rendered_name in (key_name, f'"{key_name}"', f"'{key_name}'"):
        if not stripped.startswith(rendered_name):
            continue
        remainder = stripped[len(rendered_name) :].lstrip()
        if not remainder or remainder[0] not in {"=", ":"}:
            continue
        value = remainder[1:].strip().rstrip(",").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value
    return None


def test_required_security_deployment_and_operations_artifacts_exist() -> None:
    missing = [str(path) for path in REQUIRED_ARTIFACTS if not (ROOT / path).is_file()]
    assert missing == []


def test_env_example_contains_only_placeholders_for_sensitive_names() -> None:
    values: dict[str, str] = {}
    for raw_line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip()

    unsafe = {
        name: value
        for name, value in values.items()
        if any(marker in name.upper() for marker in ("KEY", "SECRET", "TOKEN"))
        and value
        and value.lower() not in {"placeholder", "redacted", "changeme"}
        and not (value.startswith("${") and value.endswith("}"))
    }
    assert unsafe == {}


def test_tracked_release_text_contains_no_unapproved_secret_material() -> None:
    findings: list[str] = []
    private_key_markers = (
        "-----BEGIN " + "PRIVATE KEY-----",
        "-----BEGIN RSA " + "PRIVATE KEY-----",
        "-----BEGIN EC " + "PRIVATE KEY-----",
        "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
    )
    for relative_path in sorted(_tracked_text_paths()):
        text = (ROOT / relative_path).read_text(encoding="utf-8", errors="strict")
        if FAKE_SECRET_SENTINEL in text and relative_path not in FAKE_SECRET_ALLOWLIST:
            findings.append(f"{relative_path}: unapproved fake-secret sentinel")
        for marker in private_key_markers:
            if marker in text:
                findings.append(f"{relative_path}: private-key material")
        for key_name in PROVIDER_KEYS:
            for line_number, line in enumerate(text.splitlines(), start=1):
                value = _provider_assignment_value(line, key_name)
                if value and value.lower() not in {
                    "<redacted>",
                    "<set-in-secret-manager>",
                    "placeholder",
                    "redacted",
                }:
                    findings.append(f"{relative_path}:{line_number}: provider key has a value")
    assert findings == []


def test_test_server_is_loopback_only_and_reuses_production_runtime(
    tmp_path: Path,
) -> None:
    server = _load_script("run_ai4b_test_server")
    source = _artifact("scripts/run_ai4b_test_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    import_from_modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert "focusproof.api.app" in imported_names
    assert "focusproof.openhands_runtime.demo_deterministic_provider" in import_from_modules
    assert "build_demo_deterministic_test_llm" in source
    assert "SubmitEvidenceRequest" not in source
    assert "SMOKE_EVIDENCE_TEXT" not in source
    assert "create_app(" in source
    assert "uvicorn.run(" in source
    assert "DASHSCOPE_API_KEY" not in source
    assert "LLM_API_KEY" not in source
    assert "OPENAI_API_KEY" not in source

    database_url = f"sqlite+pysqlite:///{tmp_path / 'data' / 'server.sqlite3'}"
    args = server.parse_args(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--database-url",
            database_url,
            "--data-dir",
            str(tmp_path / "data"),
            "--scenario",
            "general-flow",
        ]
    )
    app = server.build_app(args)
    assert app.title == "FocusProof Agent Server"

    with pytest.raises(SystemExit):
        server.parse_args(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "8123",
                "--database-url",
                database_url,
                "--data-dir",
                str(tmp_path / "data"),
                "--scenario",
                "general-flow",
            ]
        )


def test_test_server_rejects_external_sqlite_path_before_side_effects(
    tmp_path: Path,
) -> None:
    server = _load_script("run_ai4b_test_server")
    data_dir = tmp_path / "data"
    external_database = tmp_path / "outside.sqlite3"
    args = server.parse_args(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--database-url",
            f"sqlite+pysqlite:///{external_database}",
            "--data-dir",
            str(data_dir),
            "--scenario",
            "general-flow",
        ]
    )

    with pytest.raises(
        ValueError,
        match="SQLite database path must be inside FOCUSPROOF_DATA_DIR",
    ):
        server.build_app(args)

    assert external_database.exists() is False
    assert data_dir.exists() is False


def test_scripted_smoke_rejects_non_loopback_targets() -> None:
    smoke = _load_script("ai4b_smoke")
    with pytest.raises(SystemExit):
        smoke.parse_args(
            [
                "--base-url",
                "https://public.example",
                "--scripted-review",
            ]
        )


@pytest.mark.parametrize("style", ["shell-export", "yaml", "json"])
def test_provider_secret_scanner_recognizes_common_assignment_styles(
    style: str,
) -> None:
    key_name = "OPENAI_API_KEY"
    secret = "sk-" + "live-provider-value"
    lines = {
        "shell-export": f"export {key_name}={secret}",
        "yaml": f"{key_name}: {secret}",
        "json": json.dumps({key_name: secret}),
    }
    assert _provider_assignment_value(lines[style], key_name) == secret


def test_smoke_prints_only_ids_and_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke = _load_script("ai4b_smoke")
    responses: Iterator[dict[str, Any]] = iter(
        [
            {"status": "ok", "ready": True},
            {"sessionId": "sess_safe"},
            {"evidenceId": "ev_safe", "syncPending": False},
        ]
    )

    def fake_request_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return next(responses)

    monkeypatch.setattr(smoke, "request_json", fake_request_json)
    output = io.StringIO()
    exit_code = smoke.run_smoke(
        base_url="http://127.0.0.1:8000",
        scripted_review=False,
        output=output,
    )
    rendered = output.getvalue()
    assert exit_code == 0
    assert "sess_safe" in rendered
    assert "ev_safe" in rendered
    assert smoke.SMOKE_EVIDENCE_TEXT not in rendered
    assert FAKE_SECRET_SENTINEL not in rendered
    assert not any(key in rendered for key in PROVIDER_KEYS)


def test_check_uses_argument_arrays_and_removes_provider_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = _load_script("ai4b_check")
    child_env = {key: f"value-for-{key}" for key in PROVIDER_KEYS}
    child_env["PATH"] = "/safe/bin"
    sanitized = check.build_child_env(child_env)
    assert sanitized["PATH"] == "/safe/bin"
    assert PROVIDER_KEYS.isdisjoint(sanitized)

    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool,
        stdout: int,
        stderr: int,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check
        assert stdout is subprocess.DEVNULL
        assert stderr is subprocess.DEVNULL
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(check.subprocess, "run", fake_run)
    output = io.StringIO()
    exit_code = check.run_checks(
        commands=[["python3.12", "-m", "pytest", "-q"]],
        child_env=sanitized,
        output=output,
    )
    assert exit_code == 0
    assert calls == [(["python3.12", "-m", "pytest", "-q"], sanitized)]
    assert "exit=0" in output.getvalue()


def test_docs_preserve_public_release_identity_blocker() -> None:
    combined = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in REQUIRED_ARTIFACTS
        if path.suffix == ".md"
    ).lower()
    assert "development identity" in combined
    assert "public deployment" in combined
    assert "block" in combined
    assert "production authentication" in combined
    assert "not complete" in combined or "not implemented" in combined


def test_deployment_guides_set_alembic_database_url_explicitly() -> None:
    local = _artifact("docs/deployment/LOCAL_WSL.md").read_text(encoding="utf-8")
    staging = _artifact("docs/deployment/STAGING.md").read_text(encoding="utf-8")
    for guide in (local, staging):
        assert "set_main_option" in guide
        assert "sqlalchemy.url" in guide
    assert "Alembic reads `DATABASE_URL` from its configured environment" not in local
