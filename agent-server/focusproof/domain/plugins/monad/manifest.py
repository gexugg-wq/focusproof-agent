from __future__ import annotations

from collections.abc import Mapping, Sequence

from focusproof.domain.plugins.base import PublicPluginCapability, ToolDefinitionClass
from focusproof.domain.plugins.monad.configuration import MonadPluginSettings
from focusproof.domain.plugins.monad.tool import MonadVerificationTool
from focusproof.openhands_runtime.capabilities import VerificationCapability


class MonadEvidencePluginProvider:
    plugin_id = "monad"

    def __init__(self, settings: MonadPluginSettings) -> None:
        if not settings.enabled:
            raise ValueError("Monad provider requires enabled settings")
        self.settings = settings

    def tool_definitions(self) -> Mapping[str, ToolDefinitionClass]:
        return {"MonadVerificationTool": MonadVerificationTool}

    def public_capabilities(self) -> Sequence[PublicPluginCapability]:
        return (
            PublicPluginCapability(
                plugin_id="monad",
                capability_id="monad_learning_transaction",
                enabled=True,
                metadata={
                    "chainId": self.settings.chain_id or 0,
                    "chainName": "Monad",
                    "contractAddress": self.settings.contract_address or "",
                    "explorerTxBaseUrl": self.settings.explorer_tx_base_url or "",
                    "operationLabel": "Call increment() on MonadLearningCounter",
                    "taskDescription": "Submit a wallet transaction that calls increment() on the configured teaching contract.",
                },
            ),
        )

    def capability_definitions(self) -> Sequence[VerificationCapability]:
        return (
            VerificationCapability(
                registry_name="monad_learning_transaction",
                tool_class_name="MonadVerificationTool",
                supported_evidence_types=frozenset({"monad_transaction"}),
                supported_domains=frozenset({"*"}),
                priority=30,
                read_only=True,
                requires_network=True,
                timeout_seconds=8.0,
                enabled=True,
                version="1",
            ),
        )
