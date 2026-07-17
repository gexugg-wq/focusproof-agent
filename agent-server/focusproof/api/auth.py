from __future__ import annotations

from typing import Annotated

from fastapi import Header, Request
from pydantic import BaseModel, ConfigDict, Field

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
    if getattr(request.app.state, "allow_anonymous_identity", False):
        return VerifiedIdentity(verified_user_id=DEVELOPMENT_USER_ID)

    from focusproof.api.oidc import get_token_verifier, require_verified_identity

    verifier = get_token_verifier()
    return await require_verified_identity(verifier, authorization)
