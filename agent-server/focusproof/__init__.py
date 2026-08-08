def get_project_health() -> dict[str, str]:
    return {
        "name": "focusproof-agent",
        "runtime": "python-agent-server",
        "architecture": "official-openhands-sdk-direct-reuse",
        "status": "initialized",
    }
