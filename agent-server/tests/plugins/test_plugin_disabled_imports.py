from __future__ import annotations

import builtins
from typing import Any

from focusproof.domain.plugins.loader import load_evidence_plugin_providers


def test_disabled_composition_imports_neither_monad_nor_web3(
    monkeypatch: Any,
) -> None:
    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "web3" or name.startswith("focusproof.domain.plugins.monad"):
            raise AssertionError(f"disabled plugin imported {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert load_evidence_plugin_providers({}) == ()
