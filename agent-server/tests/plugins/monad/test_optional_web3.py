from __future__ import annotations

import builtins
import importlib
import sys


def test_disabled_configuration_does_not_import_optional_web3(monkeypatch) -> None:
    real_import = builtins.__import__

    def guarded(name: str, *args: object, **kwargs: object) -> object:
        if name == "web3" or name.startswith("web3."):
            raise AssertionError("disabled plugin imported optional web3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    sys.modules.pop("focusproof.domain.plugins.monad.configuration", None)
    module = importlib.import_module("focusproof.domain.plugins.monad.configuration")
    assert module.MonadPluginSettings.from_environ({}).enabled is False


def test_rpc_transport_is_constructed_only_from_trusted_url() -> None:
    from focusproof.domain.plugins.monad.rpc_client import HttpRpcTransport

    transport = HttpRpcTransport("https://rpc.example.test")
    assert not hasattr(transport, "set_rpc_url")
