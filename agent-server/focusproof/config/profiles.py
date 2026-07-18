from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, model_validator

RuntimeProfile = Literal["deterministic-test", "local-dev", "staging", "production"]


class RealLlmPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    provider: str = Field(alias="FOCUSPROOF_LLM_PROVIDER", min_length=1)
    model: str = Field(alias="FOCUSPROOF_LLM_MODEL", min_length=1)
    base_url: str = Field(alias="FOCUSPROOF_LLM_BASE_URL", min_length=1)
    api_key: SecretStr = Field(alias="FOCUSPROOF_LLM_API_KEY")
    request_timeout_seconds: int = Field(
        alias="FOCUSPROOF_LLM_REQUEST_TIMEOUT_SECONDS", gt=0
    )
    num_retries: int = Field(alias="FOCUSPROOF_LLM_NUM_RETRIES", ge=0)
    retry_min_wait_seconds: int = Field(
        alias="FOCUSPROOF_LLM_RETRY_MIN_WAIT_SECONDS", ge=0
    )
    retry_max_wait_seconds: int = Field(
        alias="FOCUSPROOF_LLM_RETRY_MAX_WAIT_SECONDS", ge=0
    )
    context_window_tokens: int = Field(
        alias="FOCUSPROOF_LLM_CONTEXT_WINDOW_TOKENS", ge=16_384
    )
    max_output_tokens: int = Field(alias="FOCUSPROOF_LLM_MAX_OUTPUT_TOKENS", gt=0)
    max_iterations: int = Field(alias="FOCUSPROOF_LLM_MAX_ITERATIONS", gt=0)
    max_review_seconds: int = Field(alias="FOCUSPROOF_LLM_MAX_REVIEW_SECONDS", gt=0)
    max_concurrent_reviews: int = Field(
        alias="FOCUSPROOF_LLM_MAX_CONCURRENT_REVIEWS", gt=0
    )
    admission_timeout_seconds: float = Field(
        alias="FOCUSPROOF_LLM_ADMISSION_TIMEOUT_SECONDS", gt=0
    )
    max_calls_per_review: int = Field(
        alias="FOCUSPROOF_LLM_MAX_CALLS_PER_REVIEW", gt=0
    )
    max_cost_usd: float = Field(alias="FOCUSPROOF_LLM_MAX_COST_USD", gt=0)
    input_cost_per_token: float = Field(
        alias="FOCUSPROOF_LLM_INPUT_COST_PER_TOKEN", ge=0
    )
    output_cost_per_token: float = Field(
        alias="FOCUSPROOF_LLM_OUTPUT_COST_PER_TOKEN", ge=0
    )

    @model_validator(mode="after")
    def validate_retry_waits(self) -> RealLlmPolicy:
        if self.retry_max_wait_seconds < self.retry_min_wait_seconds:
            raise ValueError(
                "FOCUSPROOF_LLM_RETRY_MAX_WAIT_SECONDS must be greater than or "
                "equal to FOCUSPROOF_LLM_RETRY_MIN_WAIT_SECONDS"
            )
        return self


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile: RuntimeProfile
    real_llm: RealLlmPolicy | None


class _RealLlmEnvironment(RealLlmPolicy):
    short_context_override: None = Field(
        default=None,
        alias="ALLOW_SHORT_CONTEXT_WINDOWS",
    )


_PROFILE_ADAPTER: TypeAdapter[RuntimeProfile] = TypeAdapter(RuntimeProfile)
_REAL_LLM_KEYS = frozenset(
    field.validation_alias
    for field in RealLlmPolicy.model_fields.values()
    if isinstance(field.validation_alias, str)
)


def load_runtime_settings(environ: Mapping[str, str]) -> RuntimeSettings:
    profile = _PROFILE_ADAPTER.validate_python(
        environ.get("FOCUSPROOF_PROFILE", "local-dev")
    )
    has_real_llm_values = any(environ.get(key) for key in _REAL_LLM_KEYS)
    if profile == "deterministic-test" or (
        profile == "local-dev" and not has_real_llm_values
    ):
        return RuntimeSettings(profile=profile, real_llm=None)

    validated = _RealLlmEnvironment.model_validate(dict(environ))
    policy = RealLlmPolicy.model_validate(
        validated.model_dump(
            exclude={"short_context_override"}
        )
    )
    return RuntimeSettings(profile=profile, real_llm=policy)
