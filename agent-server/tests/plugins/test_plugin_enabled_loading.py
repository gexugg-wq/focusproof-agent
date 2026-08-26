from __future__ import annotations

from focusproof.domain.plugins.loader import load_evidence_plugin_providers


VALID_ENV = {
    "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "true",
    "FOCUSPROOF_MONAD_RPC_URL": "https://rpc.example.test",
    "FOCUSPROOF_MONAD_CHAIN_ID": "1234",
    "FOCUSPROOF_MONAD_CONTRACT_ADDRESS": "0x52908400098527886E0F7030069857D2E4169EE7",
    "FOCUSPROOF_MONAD_DEPLOYMENT_BLOCK": "42",
    "FOCUSPROOF_MONAD_EXPLORER_TX_BASE_URL": "https://explorer.example.test/tx/",
}


def test_enabled_composition_dynamically_loads_monad_provider() -> None:
    providers = load_evidence_plugin_providers(VALID_ENV)
    assert len(providers) == 1
    provider = providers[0]
    assert provider.plugin_id == "monad"
    assert set(provider.tool_definitions()) == {"MonadVerificationTool"}
    capabilities = provider.capability_definitions()
    assert len(capabilities) == 1
    assert capabilities[0].tool_class_name == "MonadVerificationTool"
    assert capabilities[0].supported_evidence_types == frozenset({"monad_transaction"})
