from __future__ import annotations

from pydantic import BaseModel, ConfigDict

DEVELOPMENT_USER_ID = "dev-anonymous-user"


class VerifiedIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    verified_user_id: str


def get_verified_identity() -> VerifiedIdentity:
    return VerifiedIdentity(verified_user_id=DEVELOPMENT_USER_ID)
