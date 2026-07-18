from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Header, Request
from pydantic import BaseModel, ConfigDict, Field

from focusproof.runtime.security_audit import (
    RequestSecurityAuditContext,
    SecurityAuditOutcome,
    SecurityAuditReasonCategory,
    SecurityAuditSink,
    compute_token_fingerprint,
    invalid_authorization_reason,
    raw_bearer_token_bytes,
)

DEVELOPMENT_USER_ID = "dev-anonymous-user"


class VerifiedIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    principal_id: str = Field(alias="verified_user_id")
    token_fingerprint: str = "anonymous"

    @property
    def verified_user_id(self) -> str:
        return self.principal_id


async def get_verified_identity(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> VerifiedIdentity:
    return await resolve_verified_identity(request, authorization=authorization)


async def resolve_verified_identity(
    request: Request,
    *,
    authorization: str | None,
) -> VerifiedIdentity:
    cached_identity = getattr(request.state, "verified_identity", None)
    if isinstance(cached_identity, VerifiedIdentity):
        return cached_identity

    if getattr(request.app.state, "allow_anonymous_identity", False):
        identity = VerifiedIdentity(verified_user_id=DEVELOPMENT_USER_ID)
        request.state.verified_identity = identity
        return identity

    from focusproof.api.oidc import (
        InvalidTokenError,
        get_token_verifier,
        require_verified_identity,
    )
    from focusproof.persistence.providers import PrincipalDisabledError

    verifier = get_token_verifier()
    try:
        identity = await require_verified_identity(verifier, authorization)
    except InvalidTokenError:
        record_security_audit(
            request,
            principal_id=None,
            token_fingerprint=_fingerprint_authorization(request, authorization),
            outcome="failure",
            reason_category=invalid_authorization_reason(authorization),
        )
        raise
    except PrincipalDisabledError:
        record_security_audit(
            request,
            principal_id=None,
            token_fingerprint=_fingerprint_authorization(request, authorization),
            outcome="failure",
            reason_category="forbidden",
        )
        raise
    context = get_security_audit_context(request)
    context.principal_id = identity.principal_id
    context.token_fingerprint = identity.token_fingerprint
    request.state.verified_identity = identity
    return identity


def get_security_audit_context(request: Request) -> RequestSecurityAuditContext:
    context = getattr(request.state, "security_audit_context", None)
    if not isinstance(context, RequestSecurityAuditContext):
        context = RequestSecurityAuditContext()
        request.state.security_audit_context = context
    return context


def record_security_audit(
    request: Request,
    *,
    principal_id: str | None = None,
    token_fingerprint: str | None = None,
    outcome: SecurityAuditOutcome,
    reason_category: SecurityAuditReasonCategory,
) -> None:
    context = get_security_audit_context(request)
    if context.recorded:
        return
    sink = getattr(request.app.state, "security_audit_sink", None)
    if not isinstance(sink, SecurityAuditSink):
        if getattr(request.app.state, "allow_anonymous_identity", False):
            return
        from focusproof.api.oidc import IdentityUnavailableError

        raise IdentityUnavailableError()
    sink.record(
        principal_id=principal_id if principal_id is not None else context.principal_id,
        request_id=context.request_id,
        token_fingerprint=(
            token_fingerprint
            if token_fingerprint is not None
            else context.token_fingerprint
        ),
        outcome=outcome,
        reason_category=reason_category,
        occurred_at=datetime.now(UTC),
    )
    context.recorded = True


def _fingerprint_authorization(
    request: Request,
    authorization: str | None,
) -> str | None:
    token = raw_bearer_token_bytes(authorization)
    if token is None:
        return None
    key = getattr(request.app.state, "security_audit_hmac_key", None)
    if not isinstance(key, str):
        from focusproof.api.oidc import IdentityUnavailableError

        raise IdentityUnavailableError()
    return compute_token_fingerprint(token, key)
