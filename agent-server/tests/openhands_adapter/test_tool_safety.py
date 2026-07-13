import pytest

from focusproof.openhands_adapter.errors import UnsafeOpenHandsToolError
from focusproof.openhands_adapter.tools import assert_openhands_tool_allowed, is_openhands_tool_allowed


def test_terminal_tool_is_disabled_by_default() -> None:
    assert is_openhands_tool_allowed("TerminalTool") is False


def test_file_editor_tool_is_disabled_by_default() -> None:
    assert is_openhands_tool_allowed("FileEditorTool") is False


def test_unsafe_tool_request_raises_clear_error() -> None:
    with pytest.raises(UnsafeOpenHandsToolError) as exc_info:
        assert_openhands_tool_allowed("TerminalTool")

    assert "TerminalTool" in str(exc_info.value)
