from __future__ import annotations

from typing import Final, Protocol

from focusproof.openhands_adapter.errors import UnsafeOpenHandsToolError
from focusproof.runtime.actions import Action
from focusproof.runtime.observations import Observation
from focusproof.tools.fake_tools import FakeTextEvidenceTool, FakeWeb3TxTool

DISABLED_OPENHANDS_TOOLS: Final[tuple[str, ...]] = (
    "TerminalTool",
    "FileEditorTool",
    "BrowserAutomation",
    "BrowserTool",
    "WorkspaceMutationTool",
    "ApplyPatchTool",
)


class _FocusProofTool(Protocol):
    def execute(self, action: Action) -> Observation: ...


SAFE_FOCUSPROOF_TOOLS: Final[dict[str, _FocusProofTool]] = {
    "FakeTextEvidenceTool": FakeTextEvidenceTool(),
    "FakeWeb3TxTool": FakeWeb3TxTool(),
}


def is_openhands_tool_allowed(tool_name: str) -> bool:
    normalized = tool_name.lower()
    return not any(disabled.lower() == normalized for disabled in DISABLED_OPENHANDS_TOOLS)


def assert_openhands_tool_allowed(tool_name: str) -> None:
    if not is_openhands_tool_allowed(tool_name):
        raise UnsafeOpenHandsToolError(f"OpenHands tool is disabled by default: {tool_name}")


def execute_focusproof_tool(action: Action) -> Observation:
    tool_name = action.toolName or ""
    assert_openhands_tool_allowed(tool_name)
    tool = SAFE_FOCUSPROOF_TOOLS.get(tool_name)
    if tool is None:
        return Observation(
            toolName=tool_name,
            status="failed",
            facts={},
            sourceRefs=action.evidenceIds,
            error=f"Unknown FocusProof tool: {tool_name}",
        )
    return tool.execute(action)
