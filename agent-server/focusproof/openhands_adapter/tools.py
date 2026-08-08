from __future__ import annotations

from typing import Final

from focusproof.openhands_adapter.errors import UnsafeOpenHandsToolError

DISABLED_OPENHANDS_TOOLS: Final[tuple[str, ...]] = (
    "TerminalTool",
    "FileEditorTool",
    "BrowserAutomation",
    "BrowserTool",
    "WorkspaceMutationTool",
    "ApplyPatchTool",
)


def is_openhands_tool_allowed(tool_name: str) -> bool:
    normalized = tool_name.lower()
    return not any(disabled.lower() == normalized for disabled in DISABLED_OPENHANDS_TOOLS)


def assert_openhands_tool_allowed(tool_name: str) -> None:
    if not is_openhands_tool_allowed(tool_name):
        raise UnsafeOpenHandsToolError(f"OpenHands tool is disabled by default: {tool_name}")
