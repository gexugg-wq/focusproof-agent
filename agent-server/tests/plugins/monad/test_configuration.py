from __future__ import annotations

import pytest

from focusproof.domain.plugins.monad.configuration import MonadPluginSettings


VALID_ENV = {
    "FOCUSPROOF_PLUGIN_MONAD_ENABLED": "true",
    "FOCUSPROOF_MONAD_RPC_URL": "https://rpc.example.test",
    "FOCUSPROOF_MONAD_CHAIN_ID": "1234",
    "FOCUSPROOF_MONAD_CONTRACT_ADDRESS": ("0x52908400098527886E0F7030069857D2E4169EE7"),
    "FOCUSPROOF_MONAD_DEPLOYMENT_BLOCK": "42",
    "FOCUSPROOF_MONAD_EXPLORER_TX_BASE_URL": "https://explorer.example.test/tx/",
}


def test_disabled_settings_require_no_monad_variables() -> None:
    settings = MonadPluginSettings.from_environ({})
    assert settings.enabled is False
    assert settings.rpc_url is None
    assert settings.contract_address is None


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"FOCUSPROOF_MONAD_RPC_URL": ""}, "RPC URL"),
        ({"FOCUSPROOF_MONAD_CHAIN_ID": "not-an-int"}, "chain ID"),
        ({"FOCUSPROOF_MONAD_CONTRACT_ADDRESS": "0x1234"}, "contract address"),
        (
            {
                "FOCUSPROOF_MONAD_EXPLORER_TX_BASE_URL": (
                    "https://user:password@explorer.example.test/tx/"
                )
            },
            "credentials",
        ),
    ],
)
def test_enabled_settings_reject_invalid_trusted_configuration(
    override: dict[str, str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        MonadPluginSettings.from_environ(VALID_ENV | override)


def test_enabled_settings_return_normalized_immutable_configuration() -> None:
    settings = MonadPluginSettings.from_environ(VALID_ENV)
    assert settings.enabled is True
    assert settings.chain_id == 1234
    assert settings.deployment_block == 42
    assert settings.contract_address == "0x52908400098527886E0F7030069857D2E4169EE7"
    assert settings.explorer_tx_base_url == "https://explorer.example.test/tx/"
