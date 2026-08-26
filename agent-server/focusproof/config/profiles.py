from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import math
from urllib.parse import urlsplit
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, model_validator

from typing import cast, get_args

RuntimeProfile = Literal[
    "deterministic-test",
    "demo-deterministic",
    "demo-real-vision",
    "local-dev",
    "staging",
    "production",
]


MediaScannerMode = Literal[
    "disabled",
    "clamd",
    "fake-clean",
    "fake-malicious",
    "fake-unavailable",
    "fake-timeout",
    "fake-error",
    "fake-unknown",
    "fake-raises",
]
_MIN_SCAN_BYTES = 10 * 1024 * 1024


def _validate_clamd_endpoint(endpoint: str) -> None:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid clamd endpoint") from None
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("invalid clamd endpoint")
    if parsed.scheme == "tcp":
        if (
            not parsed.hostname
            or port is None
            or not 1 <= port <= 65535
            or parsed.path
            or any(character.isspace() for character in parsed.hostname)
        ):
            raise ValueError("invalid clamd endpoint")
        return
    if parsed.scheme == "unix":
        if parsed.netloc or not parsed.path.startswith("/") or parsed.path == "/":
            raise ValueError("invalid clamd endpoint")
        return
    raise ValueError("invalid clamd endpoint")


@dataclass(frozen=True, slots=True)
class MediaSecurityPolicy:
    mode: MediaScannerMode
    endpoint: str | None = field(default=None, repr=False)
    connect_timeout_seconds: float = 1.0
    total_timeout_seconds: float = 10.0
    admission_timeout_seconds: float = 2.0
    max_scan_bytes: int = _MIN_SCAN_BYTES
    max_concurrent_scans: int = 4
    definitions_version: str = "composition-unverified"
    definitions_fresh_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    visual_provider_enabled: bool = False

    def __post_init__(self) -> None:
        if self.mode == "clamd":
            if not (self.endpoint and self.endpoint.strip()):
                raise ValueError("invalid clamd endpoint")
            _validate_clamd_endpoint(self.endpoint)
        if self.mode != "clamd" and self.endpoint is not None:
            raise ValueError("scanner endpoint is valid only for clamd")
        timeouts = (
            self.connect_timeout_seconds,
            self.total_timeout_seconds,
            self.admission_timeout_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in timeouts):
            raise ValueError("media scan timeouts must be finite and positive")
        if self.max_scan_bytes < _MIN_SCAN_BYTES:
            raise ValueError("media scan capacity is below source limit")
        if self.max_concurrent_scans <= 0:
            raise ValueError("media scan concurrency must be positive")
        if not self.definitions_version.strip() or self.definitions_fresh_at.tzinfo is None:
            raise ValueError("media scan definitions snapshot is invalid")

    @property
    def upload_enabled(self) -> bool:
        return self.mode != "disabled"


def load_media_security_policy(
    profile: RuntimeProfile, environ: Mapping[str, str]
) -> MediaSecurityPolicy:
    raw_mode = environ.get("FOCUSPROOF_MEDIA_SCANNER_MODE")
    if not raw_mode:
        raise ValueError("FOCUSPROOF_MEDIA_SCANNER_MODE is required")
    allowed_modes = set(get_args(MediaScannerMode))
    if raw_mode not in allowed_modes:
        raise ValueError("invalid media scanner mode")
    mode = cast(MediaScannerMode, raw_mode)
    if profile in {"staging", "production"} and mode != "clamd":
        raise ValueError("staging and production require clamd")
    return MediaSecurityPolicy(
        mode=mode,
        endpoint=environ.get("FOCUSPROOF_CLAMD_ENDPOINT") if mode == "clamd" else None,
        connect_timeout_seconds=float(
            environ.get("FOCUSPROOF_MEDIA_SCAN_CONNECT_TIMEOUT_SECONDS", "1")
        ),
        total_timeout_seconds=float(
            environ.get("FOCUSPROOF_MEDIA_SCAN_TOTAL_TIMEOUT_SECONDS", "10")
        ),
        admission_timeout_seconds=float(
            environ.get("FOCUSPROOF_MEDIA_SCAN_ADMISSION_TIMEOUT_SECONDS", "2")
        ),
        max_scan_bytes=int(
            environ.get("FOCUSPROOF_MEDIA_SCAN_MAX_BYTES", str(_MIN_SCAN_BYTES))
        ),
        max_concurrent_scans=int(
            environ.get("FOCUSPROOF_MEDIA_SCAN_MAX_CONCURRENCY", "4")
        ),
        definitions_version=environ.get(
            "FOCUSPROOF_CLAMD_DEFINITIONS_VERSION", "composition-unverified"
        ),
        definitions_fresh_at=datetime.fromisoformat(
            environ.get("FOCUSPROOF_CLAMD_DEFINITIONS_FRESH_AT", datetime.now(UTC).isoformat())
        ),
        visual_provider_enabled=False,
    )



class RealLlmPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    provider: str = Field(alias="FOCUSPROOF_LLM_PROVIDER", min_length=1)
    model: str = Field(alias="FOCUSPROOF_LLM_MODEL", min_length=1)
    supports_vision: bool = Field(
        default=False,
        alias="FOCUSPROOF_LLM_SUPPORTS_VISION",
    )
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
    local_model_cost_map: Literal["true"] = Field(
        alias="LITELLM_LOCAL_MODEL_COST_MAP"
    )
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
    if profile in {"deterministic-test", "demo-deterministic"} or (
        profile == "local-dev" and not has_real_llm_values
    ):
        return RuntimeSettings(profile=profile, real_llm=None)

    validated = _RealLlmEnvironment.model_validate(dict(environ))
    policy = RealLlmPolicy.model_validate(
        validated.model_dump(
            exclude={"local_model_cost_map", "short_context_override"}
        )
    )
    if profile == "production" and policy.supports_vision:
        policy = policy.model_copy(update={"supports_vision": False})
    return RuntimeSettings(profile=profile, real_llm=policy)
