from __future__ import annotations

import asyncio
from typing import Annotated, cast

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


def get_verified_identity(
    request: Request = cast(Request, None),
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> VerifiedIdentity:
    if request is None or getattr(request.app.state, "allow_anonymous_identity", False):
        return VerifiedIdentity(verified_user_id=DEVELOPMENT_USER_ID)

    from focusproof.api.oidc import get_token_verifier, require_verified_identity

    verifier = get_token_verifier()
    return asyncio.run(require_verified_identity(authorization, verifier))
