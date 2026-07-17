from __future__ import annotations

import asyncio
import hashlib
import hmac
from typing import Annotated, Protocol, cast

from fastapi import Depends, Header
import jwt
from jwt import InvalidTokenError as JwtInvalidTokenError
from jwt import PyJWK
from jwt import PyJWKClient, PyJWKClientError

from focusproof.api.auth import VerifiedIdentity
from focusproof.config.identity import OidcSettings


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


_token_verifier: TokenVerifier | None = None


def configure_token_verifier(verifier: TokenVerifier | None) -> None:
    global _token_verifier
    _token_verifier = verifier


def reset_token_verifier() -> None:
    configure_token_verifier(None)


class OidcTokenVerifier:
    def __init__(
        self,
        settings: OidcSettings,
        *,
        principal_resolver: PrincipalResolver,
    ) -> None:
        if not settings.enabled or settings.issuer is None or settings.jwks_uri is None:
            raise IdentityUnavailableError()
        self._settings = settings
        self._principal_resolver = principal_resolver
        self._signing_keys_by_kid: dict[str, PyJWK] = {}
        self._jwk_client = PyJWKClient(
            settings.jwks_uri,
            cache_jwk_set=False,
            lifespan=settings.jwks_cache_seconds,
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
        signing_key = self._signing_keys_by_kid.get(kid)
        try:
            if signing_key is None:
                signing_key = await asyncio.to_thread(
                    self._jwk_client.get_signing_key_from_jwt,
                    encoded_token,
                )
                self._signing_keys_by_kid[kid] = signing_key
            claims = jwt.decode(
                encoded_token,
                signing_key.key,
                algorithms=list(self._settings.allowed_algorithms),
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                leeway=self._settings.clock_skew_seconds,
                options={"require": ["iss", "aud", "sub", "exp", "iat", "nbf"]},
            )
        except (JwtInvalidTokenError, PyJWKClientError, ValueError, Exception) as exc:
            raise InvalidTokenError("token verification failed") from exc

        issuer = claims.get("iss")
        subject = claims.get("sub")
        if not isinstance(issuer, str) or not issuer:
            raise InvalidTokenError("token verification failed")
        if not isinstance(subject, str) or not subject.strip():
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
    digest = hmac.new(
        key.get_secret_value().encode("utf-8"),
        encoded_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def get_token_verifier() -> TokenVerifier:
    if _token_verifier is None:
        raise IdentityUnavailableError()
    return _token_verifier


async def require_verified_identity(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)] = cast(
        TokenVerifier,
        None,
    ),
) -> VerifiedIdentity:
    if verifier is None:
        raise IdentityUnavailableError()
    if authorization is None:
        raise InvalidTokenError("missing authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or not token or " " in token:
        raise InvalidTokenError("malformed authorization header")
    return await verifier.verify(token)
