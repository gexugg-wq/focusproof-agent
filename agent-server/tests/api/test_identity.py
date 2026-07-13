from focusproof.api.auth import DEVELOPMENT_USER_ID, get_verified_identity


def test_development_identity_is_explicit_and_stable() -> None:
    identity = get_verified_identity()

    assert DEVELOPMENT_USER_ID == "dev-anonymous-user"
    assert identity.verified_user_id == DEVELOPMENT_USER_ID
