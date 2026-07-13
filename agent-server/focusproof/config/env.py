from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENHANDS_LLM_MODEL",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_BASE_URL",
    "DASHSCOPE_MODEL",
    "OPENHANDS_SUPPRESS_BANNER",
)


def _env_file(project_root: Path | None = None) -> Path:
    return (project_root or PROJECT_ROOT) / ".env"


def _has_powershell_env_syntax(path: Path) -> bool:
    if not path.exists():
        return False
    return any(line.lstrip().startswith("$env:") for line in path.read_text(errors="replace").splitlines())


def load_project_env(project_root: Path | None = None) -> dict[str, str]:
    path = _env_file(project_root)
    if not path.exists() or _has_powershell_env_syntax(path):
        return {}
    values = dotenv_values(path)
    return {key: str(value) for key, value in values.items() if key in _ENV_KEYS and value}


def get_env_status(project_root: Path | None = None) -> dict[str, Any]:
    path = _env_file(project_root)
    has_powershell = _has_powershell_env_syntax(path)
    values = load_project_env(project_root)
    model = values.get("OPENHANDS_LLM_MODEL") or values.get("DASHSCOPE_MODEL")
    base_url = values.get("OPENAI_BASE_URL") or values.get("DASHSCOPE_BASE_URL")
    return {
        "hasOpenAIKey": bool(values.get("OPENAI_API_KEY")),
        "hasDashScopeKey": bool(values.get("DASHSCOPE_API_KEY")),
        "hasBaseUrl": bool(base_url),
        "model": model,
        "envFileExists": path.exists(),
        "dotenvFormatValid": path.exists() and not has_powershell,
        "hasPowerShellEnvSyntax": has_powershell,
    }
