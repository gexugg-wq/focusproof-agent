from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from types import ModuleType
from typing import Final

SDK_MODULES: Final[tuple[str, ...]] = (
    "openhands",
    "openhands.sdk.agent",
    "openhands.sdk.conversation",
    "openhands.sdk.tool",
    "openhands.sdk.event",
)


@dataclass(frozen=True)
class ImportedModule:
    name: str
    ok: bool
    path: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class OpenHandsSDKStatus:
    ok: bool
    mode: str
    imported_modules: dict[str, ImportedModule] = field(default_factory=dict)
    modules: dict[str, ModuleType] = field(default_factory=dict, repr=False, compare=False)
    error: str | None = None


def _module_path(module: ModuleType) -> str | None:
    module_path = getattr(module, "__file__", None)
    if module_path is not None:
        return str(module_path)
    module_paths = getattr(module, "__path__", None)
    if module_paths is None:
        return None
    first_path = next(iter(module_paths), None)
    return str(first_path) if first_path is not None else None


def _import_one(module_name: str) -> tuple[ImportedModule, ModuleType | None]:
    try:
        module = import_module(module_name)
    except Exception as exc:  # pragma: no cover - exact dependency failures vary by machine.
        return ImportedModule(name=module_name, ok=False, error=f"{type(exc).__name__}: {exc}"), None
    return ImportedModule(name=module_name, ok=True, path=_module_path(module)), module


def load_openhands_sdk() -> OpenHandsSDKStatus:
    imported: dict[str, ImportedModule] = {}
    modules: dict[str, ModuleType] = {}
    errors: list[str] = []
    for module_name in SDK_MODULES:
        status, module = _import_one(module_name)
        imported[module_name] = status
        if module is not None:
            modules[module_name] = module
        else:
            errors.append(f"{module_name}: {status.error or 'unknown import error'}")

    if not errors:
        return OpenHandsSDKStatus(ok=True, mode="direct", imported_modules=imported, modules=modules)
    if modules:
        return OpenHandsSDKStatus(
            ok=False,
            mode="partial",
            imported_modules=imported,
            modules=modules,
            error="; ".join(errors),
        )
    return OpenHandsSDKStatus(
        ok=False,
        mode="fallback",
        imported_modules=imported,
        modules=modules,
        error="; ".join(errors),
    )
