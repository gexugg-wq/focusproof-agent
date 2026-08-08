from __future__ import annotations

from collections.abc import Mapping, Sequence


from focusproof.domain.plugins.base import ToolDefinitionClass
from focusproof.domain.plugins.monad.configuration import MonadPluginSettings
from focusproof.openhands_runtime.capabilities import VerificationCapability


class MonadEvidencePluginProvider:
    """Optional provider populated with its executable tool in Task 5."""

    plugin_id = "monad"

    def __init__(self, settings: MonadPluginSettings) -> None:
        if not settings.enabled:
            raise ValueError("Monad provider requires enabled settings")
        self.settings = settings

    def tool_definitions(self) -> Mapping[str, ToolDefinitionClass]:
        return {}

    def capability_definitions(self) -> Sequence[VerificationCapability]:
        return ()
