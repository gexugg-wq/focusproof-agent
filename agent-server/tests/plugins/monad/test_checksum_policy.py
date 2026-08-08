from __future__ import annotations

import pytest

from focusproof.domain.plugins.monad.configuration import MonadPluginSettings


BASE = {
    "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "true",
    "FOCUSPROOF_MONAD_RPC_URL": "https://rpc.example.test",
    "FOCUSPROOF_MONAD_CHAIN_ID": "1234",
    "FOCUSPROOF_MONAD_CONTRACT_ADDRESS": "0x52908400098527886E0F7030069857D2E4169EE7",
    "FOCUSPROOF_MONAD_DEPLOYMENT_BLOCK": "42",
    "FOCUSPROOF_MONAD_EXPLORER_TX_BASE_URL": "https://explorer.example.test/tx/",
}


def test_trusted_contract_accepts_valid_mixed_case_eip55() -> None:
    settings = MonadPluginSettings.from_environ(BASE)
    assert settings.contract_address == BASE["FOCUSPROOF_MONAD_CONTRACT_ADDRESS"]


def test_trusted_contract_rejects_wrong_mixed_case_checksum() -> None:
    invalid = "0x52908400098527886E0F7030069857D2E4169Ee7"
    with pytest.raises(ValueError, match="EIP-55"):
        MonadPluginSettings.from_environ(
            BASE | {"FOCUSPROOF_MONAD_CONTRACT_ADDRESS": invalid}
        )
