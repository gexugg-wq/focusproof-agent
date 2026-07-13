from __future__ import annotations

from focusproof.openhands_adapter.tools import DISABLED_OPENHANDS_TOOLS

_REQUIRED_DISABLED = {
    "TerminalTool",
    "FileEditorTool",
    "BrowserAutomation",
    "WorkspaceMutationTool",
    "ApplyPatchTool",
}


def list_disabled_openhands_tools() -> list[str]:
    return list(DISABLED_OPENHANDS_TOOLS)


def assert_debug_tool_policy() -> None:
    disabled = set(DISABLED_OPENHANDS_TOOLS)
    missing = sorted(_REQUIRED_DISABLED - disabled)
    if missing:
        raise AssertionError(f"Debug tool policy missing disabled tools: {', '.join(missing)}")
