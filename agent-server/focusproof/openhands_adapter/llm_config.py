from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openhands.sdk import LLM
from pydantic import SecretStr

from focusproof.config.env import get_env_status, load_project_env
from focusproof.config.profiles import RealLlmPolicy


@dataclass(frozen=True)
class OpenHandsLLMConfig:
    model: str
    api_key: SecretStr
    base_url: str | None
    provider_hint: str


def build_openhands_llm(policy: RealLlmPolicy, usage_id: str) -> LLM:
    return LLM(
        usage_id=usage_id,
        model=policy.model,
        api_key=policy.api_key,
        base_url=policy.base_url,
        num_retries=policy.num_retries,
        retry_min_wait=policy.retry_min_wait_seconds,
        retry_max_wait=policy.retry_max_wait_seconds,
        timeout=policy.request_timeout_seconds,
        max_input_tokens=policy.context_window_tokens,
        max_output_tokens=policy.max_output_tokens,
        input_cost_per_token=policy.input_cost_per_token,
        output_cost_per_token=policy.output_cost_per_token,
        log_completions=False,
        stream=False,
    )


def build_openhands_llm_config(project_root: Path | None = None) -> OpenHandsLLMConfig | None:
    values = load_project_env(project_root)
    api_key = values.get("OPENAI_API_KEY") or values.get("DASHSCOPE_API_KEY")
    if not api_key:
        return None
    model = values.get("OPENHANDS_LLM_MODEL") or values.get("DASHSCOPE_MODEL") or "gpt-4.1-mini"
    base_url = values.get("OPENAI_BASE_URL") or values.get("DASHSCOPE_BASE_URL")
    provider_hint = "dashscope-openai-compatible" if values.get("DASHSCOPE_API_KEY") else "openai-compatible"
    return OpenHandsLLMConfig(
        model=model,
        api_key=SecretStr(api_key),
        base_url=base_url,
        provider_hint=provider_hint,
    )


def get_llm_config_status(project_root: Path | None = None) -> dict[str, Any]:
    env_status = get_env_status(project_root)
    config = build_openhands_llm_config(project_root)
    missing_fields: list[str] = []
    if not (env_status["hasOpenAIKey"] or env_status["hasDashScopeKey"]):
        missing_fields.append("credential")
    if not env_status["model"]:
        missing_fields.append("model")
    provider_hint = None
    if config is not None:
        provider_hint = config.provider_hint
    elif env_status["hasDashScopeKey"]:
        provider_hint = "dashscope-openai-compatible"
    elif env_status["hasOpenAIKey"]:
        provider_hint = "openai-compatible"
    return {
        "canBuildConfig": config is not None,
        "model": config.model if config else env_status["model"],
        "providerHint": provider_hint,
        "missingFields": missing_fields,
        "envFileExists": env_status["envFileExists"],
        "dotenvFormatValid": env_status["dotenvFormatValid"],
    }
