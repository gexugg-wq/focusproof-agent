import asyncio
from types import SimpleNamespace

from fastapi import Request

from focusproof.api.auth import DEVELOPMENT_USER_ID, get_verified_identity


def test_development_identity_is_explicit_and_stable() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "root_path": "",
            "app": SimpleNamespace(
                state=SimpleNamespace(allow_anonymous_identity=True)
            ),
        }
    )
    identity = asyncio.run(get_verified_identity(request))

    assert DEVELOPMENT_USER_ID == "dev-anonymous-user"
    assert identity.verified_user_id == DEVELOPMENT_USER_ID
