from __future__ import annotations

import pytest
from pydantic import ValidationError

from focusproof.config.profiles import load_runtime_settings


def complete_fake_dashscope_environment() -> dict[str, str]:
    return {
        "FOCUSPROOF_PROFILE": "staging",
        "FOCUSPROOF_LLM_PROVIDER": "openai-compatible",
        "FOCUSPROOF_LLM_MODEL": "qwen-plus",
        "FOCUSPROOF_LLM_BASE_URL": (
            "https://dashscope.example.test/compatible-mode/v1"
        ),
        "FOCUSPROOF_LLM_API_KEY": "placeholder",
        "FOCUSPROOF_LLM_REQUEST_TIMEOUT_SECONDS": "30",
        "FOCUSPROOF_LLM_NUM_RETRIES": "1",
        "FOCUSPROOF_LLM_RETRY_MIN_WAIT_SECONDS": "1",
        "FOCUSPROOF_LLM_RETRY_MAX_WAIT_SECONDS": "4",
        "FOCUSPROOF_LLM_MAX_INPUT_TOKENS": "8192",
        "FOCUSPROOF_LLM_MAX_OUTPUT_TOKENS": "1024",
        "FOCUSPROOF_LLM_MAX_ITERATIONS": "6",
        "FOCUSPROOF_LLM_MAX_REVIEW_SECONDS": "60",
        "FOCUSPROOF_LLM_MAX_CONCURRENT_REVIEWS": "1",
        "FOCUSPROOF_LLM_ADMISSION_TIMEOUT_SECONDS": "1",
        "FOCUSPROOF_LLM_MAX_CALLS_PER_REVIEW": "4",
        "FOCUSPROOF_LLM_MAX_COST_USD": "0.10",
        "FOCUSPROOF_LLM_INPUT_COST_PER_TOKEN": "0.000001",
        "FOCUSPROOF_LLM_OUTPUT_COST_PER_TOKEN": "0.000002",
        "LITELLM_LOCAL_MODEL_COST_MAP": "true",
    }


def test_deterministic_profile_ignores_provider_values() -> None:
    settings = load_runtime_settings(
        {
            "FOCUSPROOF_PROFILE": "deterministic-test",
            "DASHSCOPE_API_KEY": "placeholder",
        }
    )

    assert settings.profile == "deterministic-test"
    assert settings.real_llm is None


def test_staging_profile_requires_every_real_llm_bound() -> None:
    with pytest.raises(ValidationError, match="FOCUSPROOF_LLM_MODEL"):
        load_runtime_settings({"FOCUSPROOF_PROFILE": "staging"})


def test_staging_profile_builds_provider_neutral_policy() -> None:
    settings = load_runtime_settings(complete_fake_dashscope_environment())

    assert settings.real_llm is not None
    assert settings.real_llm.provider == "openai-compatible"
    assert settings.real_llm.api_key.get_secret_value() == "placeholder"
    assert "placeholder" not in settings.model_dump_json()
