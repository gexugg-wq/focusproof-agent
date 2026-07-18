from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
import jwt
from openhands.sdk.testing import TestLLM

from focusproof.api import app as app_module
from focusproof.api.oidc import PrincipalResolver


@dataclass(frozen=True, slots=True)
class LocalOidcFixture:
    issuer: str
    audience: str
    private_key_pem: bytes
    public_jwk: dict[str, object]
    kid: str

    def token(
        self,
        *,
        subject: str = "subject-a",
        expires_delta_seconds: int = 300,
        not_before_delta_seconds: int = -1,
        issuer: str | None = None,
        audience: str | None = None,
        algorithm: str = "RS256",
        kid: str | None = None,
        additional_claims: dict[str, object] | None = None,
    ) -> str:
        import time

        now = int(time.time())
        payload: dict[str, object] = {
            "iss": issuer or self.issuer,
            "aud": audience or self.audience,
            "sub": subject,
            "iat": now,
            "nbf": now + not_before_delta_seconds,
            "exp": now + expires_delta_seconds,
        }
        if additional_claims is not None:
            payload.update(additional_claims)
        key: bytes | str = self.private_key_pem
        headers = {"kid": kid or self.kid}
        if algorithm.startswith("HS"):
            key = sha256(self.private_key_pem).hexdigest()
        return str(jwt.encode(payload, key, algorithm=algorithm, headers=headers))


@dataclass(frozen=True, slots=True)
class StaticPrincipalResolver:
    principal_id: str = "principal_test_owner"

    def resolve(self, *, issuer: str, subject: str) -> str:
        if not issuer or not subject:
            raise ValueError("issuer and subject are required")
        return self.principal_id


def local_oidc_fixture() -> LocalOidcFixture:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_jwk = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
    )
    kid = sha256(
        json.dumps(public_jwk, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    public_jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return LocalOidcFixture(
        issuer="https://focusproof-issuer.example.test",
        audience="focusproof-api",
        private_key_pem=private_key_pem,
        public_jwk=public_jwk,
        kid=kid,
    )


def oidc_test_app(
    tmp_path: Path,
    fixture: LocalOidcFixture,
    *,
    principal_resolver: PrincipalResolver | None = None,
    llm_factory: Callable[[str], TestLLM] | None = None,
) -> FastAPI:
    project_root = Path(__file__).resolve().parents[3]
    database_url = f"sqlite+pysqlite:///{tmp_path / 'ai4c-identity.sqlite3'}"
    config = Config(project_root / "alembic.ini")
    config.set_main_option(
        "script_location",
        str(project_root / "agent-server/migrations"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = app_module.create_app(
        database_url=database_url,
        data_dir=tmp_path,
        llm_factory=llm_factory or (lambda session_id: TestLLM.from_messages([])),
        principal_resolver=principal_resolver,
    )

    @app.get("/__test__/oidc/jwks")
    def test_jwks() -> dict[str, list[dict[str, object]]]:
        return {"keys": [fixture.public_jwk]}

    @app.get("/__test__/oidc/issuer")
    def test_issuer() -> dict[str, Any]:
        return {
            "issuer": fixture.issuer,
            "jwks_uri": "https://testserver/__test__/oidc/jwks",
        }

    return app
