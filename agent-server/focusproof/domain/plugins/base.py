from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from openhands.sdk.tool import ToolDefinition

if TYPE_CHECKING:
    from focusproof.openhands_runtime.capabilities import VerificationCapability


ToolDefinitionClass = type[ToolDefinition[Any, Any]]


class EvidencePluginProvider(Protocol):
    """Core-neutral contribution to the official OpenHands tool registry."""

    plugin_id: str

    def tool_definitions(self) -> Mapping[str, ToolDefinitionClass]: ...

    def capability_definitions(self) -> Sequence[VerificationCapability]: ...
