import os


# Application security invariant: LiteLLM must use its bundled cost map and must
# never fetch pricing metadata during FocusProof imports or startup.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "true"


def get_project_health() -> dict[str, str]:
    return {
        "name": "focusproof-agent",
        "runtime": "python-agent-server",
        "architecture": "openhands-inspired",
        "status": "initialized",
    }
