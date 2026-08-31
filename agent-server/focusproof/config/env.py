from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from focusproof.speech_core.models import (
    SPEECH_ACCEPTED_FORMATS,
    SpeechCapability,
    SpeechSettings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENHANDS_LLM_MODEL",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_BASE_URL",
    "DASHSCOPE_MODEL",
    "OPENHANDS_SUPPRESS_BANNER",
    "FOCUSPROOF_ASR_PROVIDER",
    "FOCUSPROOF_ASR_MODEL",
    "FOCUSPROOF_ASR_BASE_URL",
    "FOCUSPROOF_ASR_E2E_TIMEOUT_SECONDS",
    "FOCUSPROOF_ASR_MAX_CONCURRENCY",
    "FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_ACTIVE_VERSION",
    "FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_KEYRING_JSON",
)
_ASR_ACTIVATION_KEYS = tuple(key for key in _ENV_KEYS if key.startswith("FOCUSPROOF_ASR_"))


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


def load_speech_settings(environ: Mapping[str, str]) -> SpeechSettings | None:
    if not any(environ.get(key) for key in _ASR_ACTIVATION_KEYS):
        return None
    required = (
        "FOCUSPROOF_ASR_PROVIDER",
        "FOCUSPROOF_ASR_MODEL",
        "FOCUSPROOF_ASR_BASE_URL",
        "DASHSCOPE_API_KEY",
        "FOCUSPROOF_ASR_E2E_TIMEOUT_SECONDS",
        "FOCUSPROOF_ASR_MAX_CONCURRENCY",
        "FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_ACTIVE_VERSION",
        "FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_KEYRING_JSON",
    )
    if any(not environ.get(key) for key in required):
        raise ValueError("speech configuration is incomplete")
    try:
        e2e_timeout_seconds = int(environ["FOCUSPROOF_ASR_E2E_TIMEOUT_SECONDS"])
        max_concurrency = int(environ["FOCUSPROOF_ASR_MAX_CONCURRENCY"])
    except (TypeError, ValueError) as exc:
        raise ValueError("speech numeric configuration is invalid") from exc
    if (
        environ["FOCUSPROOF_ASR_PROVIDER"] != "dashscope"
        or environ["FOCUSPROOF_ASR_MODEL"] != "qwen3-asr-flash"
    ):
        raise ValueError("speech provider or model is invalid")
    try:
        decoded_keyring = json.loads(
            environ["FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_KEYRING_JSON"]
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("speech HMAC configuration is invalid") from exc
    active_hmac_version = environ[
        "FOCUSPROOF_SPEECH_IDEMPOTENCY_HMAC_ACTIVE_VERSION"
    ]
    if (
        not isinstance(decoded_keyring, dict)
        or not decoded_keyring
        or active_hmac_version not in decoded_keyring
        or any(
            not isinstance(version, str)
            or not version.strip()
            or not isinstance(key, str)
            or not key.strip()
            for version, key in decoded_keyring.items()
        )
    ):
        raise ValueError("speech HMAC configuration is invalid")
    return SpeechSettings(
        provider="dashscope",
        model="qwen3-asr-flash",
        base_url=environ["FOCUSPROOF_ASR_BASE_URL"],
        api_key=environ["DASHSCOPE_API_KEY"],
        idempotency_hmac_active_version=active_hmac_version,
        idempotency_hmac_keyring=tuple(decoded_keyring.items()),
        e2e_timeout_seconds=e2e_timeout_seconds,
        max_concurrency=max_concurrency,
    )


def build_speech_capability(environ: Mapping[str, str]) -> SpeechCapability:
    try:
        settings = load_speech_settings(environ)
    except (TypeError, ValueError):
        return {
            "capabilityId": "speech_transcription",
            "schemaVersion": 1,
            "enabled": False,
            "reasonCode": "asr_configuration_invalid",
        }
    if settings is None:
        return {
            "capabilityId": "speech_transcription",
            "schemaVersion": 1,
            "enabled": False,
            "reasonCode": "asr_not_configured",
        }
    return {
        "capabilityId": "speech_transcription",
        "schemaVersion": 1,
        "enabled": True,
        "formats": list(SPEECH_ACCEPTED_FORMATS),
        "maxAudioBytes": 10_485_760,
        "maxDurationSeconds": 120,
        "languageHintsAccepted": ["auto", "zh", "en"],
        "languageHintEffect": "metadata_only",
    }
