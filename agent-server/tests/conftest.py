from __future__ import annotations

import os

from focusproof.config.cost_map import prepare_openhands_cost_map


# This root conftest is imported before suite-specific conftests that import
# OpenHands/LiteLLM. Run the product preflight under the deterministic test
# profile, then restore the profile whose application behavior a test intends
# to exercise. The bundled cost-map invariant remains set for later imports.
_original_profile = os.environ.get("FOCUSPROOF_PROFILE")
os.environ["FOCUSPROOF_PROFILE"] = "deterministic-test"
prepare_openhands_cost_map()
if _original_profile is None:
    os.environ.pop("FOCUSPROOF_PROFILE")
else:
    os.environ["FOCUSPROOF_PROFILE"] = _original_profile
