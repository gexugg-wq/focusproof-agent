from focusproof.openhands_adapter.capabilities import get_openhands_capabilities
from focusproof.openhands_adapter.sdk_imports import load_openhands_sdk
from focusproof.openhands_adapter.tools import DISABLED_OPENHANDS_TOOLS


def test_openhands_sdk_import_status_is_structured() -> None:
    status = load_openhands_sdk()

    assert isinstance(status.imported_modules, dict)
    assert "openhands" in status.imported_modules
    assert status.mode in {"direct", "partial", "fallback"}
    if status.ok:
        assert status.imported_modules["openhands"].path
    else:
        assert status.error


def test_get_openhands_capabilities_returns_stable_shape() -> None:
    capabilities = get_openhands_capabilities()

    assert isinstance(capabilities["importOk"], bool)
    assert capabilities["adapterMode"] in {"direct", "partial", "fallback"}
    assert "hasAgent" in capabilities
    assert "hasConversation" in capabilities
    assert "hasTool" in capabilities
    assert "hasEvent" in capabilities
    assert "hasActionObservation" in capabilities
    assert "disabledTools" in capabilities


def test_disabled_tool_list_contains_dangerous_openhands_tools() -> None:
    assert "TerminalTool" in DISABLED_OPENHANDS_TOOLS
    assert "FileEditorTool" in DISABLED_OPENHANDS_TOOLS
    assert "BrowserAutomation" in DISABLED_OPENHANDS_TOOLS
    assert "WorkspaceMutationTool" in DISABLED_OPENHANDS_TOOLS
