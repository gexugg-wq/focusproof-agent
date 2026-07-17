from __future__ import annotations

from collections.abc import Mapping

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    field_validator,
)

from focusproof.config.profiles import RuntimeProfile

_PROFILE_ADAPTER: TypeAdapter[RuntimeProfile] = TypeAdapter(RuntimeProfile)


class OidcSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
    issuer: str | None
    audience: str | None
    jwks_uri: str | None
    allowed_algorithms: tuple[str, ...] = ("RS256",)
    clock_skew_seconds: int = 30
    jwks_cache_seconds: int = 300
    principal_fingerprint_key: SecretStr | None


class _OidcEnvironment(BaseModel):
    model_config = ConfigDict(frozen=True)

    issuer: str = Field(alias="FOCUSPROOF_OIDC_ISSUER", min_length=1)
    audience: str = Field(alias="FOCUSPROOF_OIDC_AUDIENCE", min_length=1)
    jwks_uri: str = Field(alias="FOCUSPROOF_OIDC_JWKS_URI", min_length=1)
    allowed_algorithms: tuple[str, ...] = Field(
        default=("RS256",),
        alias="FOCUSPROOF_OIDC_ALLOWED_ALGORITHMS",
    )
    clock_skew_seconds: int = Field(
        default=30,
        alias="FOCUSPROOF_OIDC_CLOCK_SKEW_SECONDS",
        ge=0,
    )
    jwks_cache_seconds: int = Field(
        default=300,
        alias="FOCUSPROOF_OIDC_JWKS_CACHE_SECONDS",
        gt=0,
    )
    principal_fingerprint_key: SecretStr = Field(
        alias="FOCUSPROOF_OIDC_FINGERPRINT_KEY"
    )

    @field_validator("allowed_algorithms", mode="before")
    @classmethod
    def parse_algorithms(
        cls,
        value: object,
    ) -> tuple[str, ...]:
        if isinstance(value, tuple):
            items = [item.strip() for item in value if isinstance(item, str)]
        elif isinstance(value, list):
            items = [str(item).strip() for item in value]
        elif isinstance(value, str):
            items = [item.strip() for item in value.split(",")]
        else:
            raise TypeError("FOCUSPROOF_OIDC_ALLOWED_ALGORITHMS must be a string")
        normalized = tuple(item for item in items if item)
        if not normalized:
            raise ValueError(
                "FOCUSPROOF_OIDC_ALLOWED_ALGORITHMS must contain at least one value"
            )
        return normalized

    @field_validator("issuer", "jwks_uri", mode="before")
    @classmethod
    def validate_http_url(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("OIDC URL values must be strings")
        normalized = value.strip()
        if not normalized:
            raise ValueError("OIDC URL values must not be empty")
        TypeAdapter(AnyHttpUrl).validate_python(normalized)
        return normalized


def load_oidc_settings(
    environ: Mapping[str, str],
    *,
    profile: str,
) -> OidcSettings:
    runtime_profile = _PROFILE_ADAPTER.validate_python(profile)
    if runtime_profile in {"deterministic-test", "local-dev"}:
        return OidcSettings(
            enabled=False,
            issuer=None,
            audience=None,
            jwks_uri=None,
            principal_fingerprint_key=None,
        )

    validated = _OidcEnvironment.model_validate(dict(environ))
    return OidcSettings(
        enabled=True,
        issuer=validated.issuer,
        audience=validated.audience,
        jwks_uri=validated.jwks_uri,
        allowed_algorithms=validated.allowed_algorithms,
        clock_skew_seconds=validated.clock_skew_seconds,
        jwks_cache_seconds=validated.jwks_cache_seconds,
        principal_fingerprint_key=validated.principal_fingerprint_key,
    )
