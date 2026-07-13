from focusproof import get_project_health


def test_project_health_metadata() -> None:
    health = get_project_health()

    assert health["name"] == "focusproof-agent"
    assert health["runtime"] == "python-agent-server"
    assert "openhands" in health["architecture"]
    assert health["status"] == "initialized"
