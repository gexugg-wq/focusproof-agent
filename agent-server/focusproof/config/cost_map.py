from __future__ import annotations

from collections.abc import MutableMapping
import os


COST_MAP_ENVIRONMENT_KEY = "LITELLM_LOCAL_MODEL_COST_MAP"
_LOCAL_PROFILES = frozenset(
    {
        "local-dev",
        "deterministic-test",
        "demo-deterministic",
        "demo-real-vision",
    }
)
_NON_LOCAL_PROFILES = frozenset({"staging", "production"})


class CostMapPreflightError(RuntimeError):
    """Raised before OpenHands imports when the cost-map invariant is unsafe."""


def prepare_openhands_cost_map(
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Enforce bundled LiteLLM pricing data at the OpenHands import boundary."""
    values = os.environ if environ is None else environ
    profile = values.get("FOCUSPROOF_PROFILE", "local-dev")
    if profile in _NON_LOCAL_PROFILES:
        if values.get(COST_MAP_ENVIRONMENT_KEY) != "true":
            raise CostMapPreflightError("local model cost map is required")
        return
    if profile in _LOCAL_PROFILES:
        values[COST_MAP_ENVIRONMENT_KEY] = "true"
        return
    raise CostMapPreflightError("runtime profile is invalid")
