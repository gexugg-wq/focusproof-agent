from __future__ import annotations

from typing import Any

from focusproof.openhands_adapter.sdk_imports import load_openhands_sdk
from focusproof.openhands_adapter.tools import DISABLED_OPENHANDS_TOOLS


def get_openhands_capabilities() -> dict[str, Any]:
    status = load_openhands_sdk()
    imported = status.imported_modules
    tool_module_status = imported.get("openhands.sdk.tool")
    has_action_observation = False
    tool_module = status.modules.get("openhands.sdk.tool")
    if tool_module is not None:
        has_action_observation = hasattr(tool_module, "Action") and hasattr(tool_module, "Observation")

    return {
        "importOk": status.ok,
        "adapterMode": status.mode,
        "error": status.error,
        "importedModules": {
            name: {"ok": module.ok, "path": module.path, "error": module.error}
            for name, module in imported.items()
        },
        "hasAgent": bool(imported.get("openhands.sdk.agent") and imported["openhands.sdk.agent"].ok),
        "hasConversation": bool(
            imported.get("openhands.sdk.conversation") and imported["openhands.sdk.conversation"].ok
        ),
        "hasTool": bool(tool_module_status and tool_module_status.ok),
        "hasEvent": bool(imported.get("openhands.sdk.event") and imported["openhands.sdk.event"].ok),
        "hasActionObservation": has_action_observation,
        "disabledTools": list(DISABLED_OPENHANDS_TOOLS),
    }
