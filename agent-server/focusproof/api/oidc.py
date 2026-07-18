from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Annotated, Callable, Protocol

from fastapi import Depends, Header
import jwt
from jwt import PyJWK
from jwt import PyJWKClient

from focusproof.api.auth import VerifiedIdentity
from focusproof.config.identity import OidcSettings
from focusproof.runtime.security_audit import compute_token_fingerprint


class InvalidTokenError(RuntimeError):
    pass


class IdentityUnavailableError(RuntimeError):
    def __init__(self, code: str = "identity_unavailable") -> None:
        super().__init__(code)
        self.code = code


class TokenVerifier(Protocol):
    async def verify(self, encoded_token: str) -> VerifiedIdentity: ...


class PrincipalResolver(Protocol):
    def resolve(self, *, issuer: str, subject: str) -> str: ...


@dataclass(frozen=True, slots=True)
class _CachedSigningKey:
    signing_key: PyJWK
    expires_at: float


class _SigningKeyCache:
    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float],
        ttl_seconds: int,
        max_entries: int,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._monotonic_clock = monotonic_clock
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, _CachedSigningKey] = OrderedDict()

    def get(self, kid: str) -> PyJWK | None:
        entry = self._entries.get(kid)
        if entry is None:
            return None
        if entry.expires_at <= self._monotonic_clock():
            del self._entries[kid]
            return None
        self._entries.move_to_end(kid)
        return entry.signing_key

    def put(self, kid: str, signing_key: PyJWK) -> None:
        self._entries[kid] = _CachedSigningKey(
            signing_key=signing_key,
            expires_at=self._monotonic_clock() + self._ttl_seconds,
        )
        self._entries.move_to_end(kid)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


_token_verifier: TokenVerifier | None = None


def configure_token_verifier(verifier: TokenVerifier | None) -> None:
    global _token_verifier
    _token_verifier = verifier


def reset_token_verifier() -> None:
    configure_token_verifier(None)


class OidcTokenVerifier:
    DEFAULT_MAX_CACHED_SIGNING_KEYS = 32

    def __init__(
        self,
        settings: OidcSettings,
        *,
        principal_resolver: PrincipalResolver,
        monotonic_clock: Callable[[], float] = monotonic,
        max_cached_signing_keys: int = DEFAULT_MAX_CACHED_SIGNING_KEYS,
    ) -> None:
        if not settings.enabled or settings.issuer is None or settings.jwks_uri is None:
            raise IdentityUnavailableError()
        self._settings = settings
        self._principal_resolver = principal_resolver
        self._signing_key_cache = _SigningKeyCache(
            monotonic_clock=monotonic_clock,
            ttl_seconds=settings.jwks_cache_seconds,
            max_entries=max_cached_signing_keys,
        )
        self._jwk_client = PyJWKClient(
            settings.jwks_uri,
            cache_jwk_set=False,
            timeout=5,
        )

    async def verify(self, encoded_token: str) -> VerifiedIdentity:
        try:
            header = jwt.get_unverified_header(encoded_token)
        except Exception as exc:
            raise InvalidTokenError("token verification failed") from exc
        algorithm = header.get("alg")
        kid = header.get("kid")
        if algorithm not in self._settings.allowed_algorithms:
            raise InvalidTokenError("token verification failed")
        if not isinstance(kid, str) or not kid:
            raise InvalidTokenError("token verification failed")
        signing_key = self._signing_key_cache.get(kid)
        try:
            if signing_key is None:
                signing_key = await asyncio.to_thread(
                    self._jwk_client.get_signing_key_from_jwt,
                    encoded_token,
                )
                self._signing_key_cache.put(kid, signing_key)
            claims = jwt.decode(
                encoded_token,
                signing_key.key,
                algorithms=list(self._settings.allowed_algorithms),
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                leeway=self._settings.clock_skew_seconds,
                options={"require": ["iss", "aud", "sub", "exp", "iat", "nbf"]},
            )
        except Exception as exc:
            raise InvalidTokenError("token verification failed") from exc

        issuer = claims.get("iss")
        subject = claims.get("sub")
        if not isinstance(issuer, str) or not issuer:
            raise InvalidTokenError("token verification failed")
        if (
            not isinstance(subject, str)
            or not subject.strip()
            or subject != subject.strip()
        ):
            raise InvalidTokenError("token verification failed")

        return VerifiedIdentity(
            verified_user_id=self._principal_resolver.resolve(
                issuer=issuer,
                subject=subject,
            ),
            token_fingerprint=_fingerprint_token(
                encoded_token,
                self._settings,
            ),
        )


def _fingerprint_token(
    encoded_token: str,
    settings: OidcSettings,
) -> str:
    key = settings.principal_fingerprint_key
    if key is None:
        raise IdentityUnavailableError()
    return compute_token_fingerprint(
        encoded_token.encode("utf-8"),
        key.get_secret_value(),
    )


def get_token_verifier() -> TokenVerifier:
    if _token_verifier is None:
        raise IdentityUnavailableError()
    return _token_verifier


async def require_verified_identity(
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> VerifiedIdentity:
    if authorization is None:
        raise InvalidTokenError("missing authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or not token or " " in token:
        raise InvalidTokenError("malformed authorization header")
    return await verifier.verify(token)
