from __future__ import annotations

from collections.abc import Mapping, Sequence

from focusproof.domain.plugins.base import PublicPluginCapability, ToolDefinitionClass
from focusproof.domain.plugins.monad.configuration import MonadPluginSettings
from focusproof.domain.plugins.monad.rpc_client import BoundedMonadRpcClient, HttpRpcTransport
from focusproof.domain.plugins.monad.session_repository import BoundMonadSessionRepository
from focusproof.domain.plugins.monad.tool import MonadVerificationTool
from focusproof.domain.plugins.monad.verifier import MonadEvidenceVerifier
from focusproof.openhands_runtime.capabilities import VerificationCapability
from focusproof.openhands_runtime.tools import SessionEvidenceRepository
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike


class MonadEvidencePluginProvider:
    plugin_id = "monad"

    def __init__(self, settings: MonadPluginSettings) -> None:
        if not settings.enabled:
            raise ValueError("Monad provider requires enabled settings")
        self.settings = settings

    def tool_definitions(self) -> Mapping[str, ToolDefinitionClass]:
        return {"MonadVerificationTool": MonadVerificationTool}

    def normalize_evidence_submission(self, request: object) -> object:
        from focusproof.api.models import SubmitEvidenceRequest

        if not isinstance(request, SubmitEvidenceRequest):
            return request
        if request.evidenceType != "monad_transaction":
            return request
        metadata = dict(request.metadata)
        raw_explanation = metadata.get("operationExplanation")
        explanation = raw_explanation.strip() if isinstance(raw_explanation, str) else ""
        if not explanation:
            return request
        return request.model_copy(update={"textContent": explanation, "metadata": metadata})

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

    def bind_session_repository(
        self,
        repository: SessionEvidenceRepository,
        *,
        session_id: str,
        principal_id: str,
        uow_factory: UnitOfWorkFactoryLike,
    ) -> BoundMonadSessionRepository:
        del session_id
        return BoundMonadSessionRepository.bind(
            repository,
            verifier=self._build_verifier(),
            principal_id=principal_id,
            uow_factory=uow_factory,
        )

    def _build_verifier(self) -> MonadEvidenceVerifier:
        transport = HttpRpcTransport(self.settings.rpc_url or "")
        rpc = BoundedMonadRpcClient(transport)
        return MonadEvidenceVerifier(
            rpc=rpc,
            chain_id=self.settings.chain_id or 0,
            contract_address=self.settings.contract_address or "",
            deployment_block=self.settings.deployment_block or 0,
            increment_selector=_selector("increment()")[:10],
            incremented_topic=_selector("Incremented(address,uint256,uint256)"),
        )


def _selector(signature: str) -> str:
    try:
        from eth_utils.crypto import keccak

        return "0x" + keccak(text=signature).hex()
    except ImportError:
        from web3 import Web3

        return str(Web3.keccak(text=signature).hex())
