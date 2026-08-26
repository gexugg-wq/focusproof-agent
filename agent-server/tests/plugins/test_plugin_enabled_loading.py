from __future__ import annotations

from focusproof.domain.plugins.loader import load_evidence_plugin_providers


LEGACY_MONAD_ENV = {
    "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "true",
    "FOCUSPROOF_MONAD_RPC_URL": "https://rpc.example.test",
    "FOCUSPROOF_MONAD_CHAIN_ID": "1234",
    "FOCUSPROOF_MONAD_CONTRACT_ADDRESS": "0x52908400098527886E0F7030069857D2E4169EE7",
    "FOCUSPROOF_MONAD_DEPLOYMENT_BLOCK": "42",
    "FOCUSPROOF_MONAD_EXPLORER_TX_BASE_URL": "https://explorer.example.test/tx/",
}


def test_legacy_monad_environment_does_not_load_provider() -> None:
    assert load_evidence_plugin_providers(LEGACY_MONAD_ENV) == ()
