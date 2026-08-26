from __future__ import annotations

from collections.abc import Collection, Iterable
from hashlib import sha256
from typing import Any

from openhands.sdk.tool import Tool, ToolDefinition, list_registered_tools, register_tool

from focusproof.domain.plugins.base import EvidencePluginProvider
from focusproof.openhands_runtime.capabilities import (
    VerificationCapability,
    VerificationCapabilityRegistry,
)
from focusproof.openhands_runtime.runtime_contributions import RuntimeContribution
from focusproof.openhands_runtime.tools import SessionEvidenceRepository

_CONTROL_TOOL_CLASSES = (
    "FocusProofLearnerInputTool",
    "FocusProofReviewDraftTool",
)
_COMPATIBILITY_VERIFIER_TOOL_CLASS = "FocusProofEvidenceVerificationTool"


class SessionToolAssembler:
    def __init__(
        self,
        registry: VerificationCapabilityRegistry,
        *,
        plugin_providers: Iterable[EvidencePluginProvider] = (),
        runtime_contributions: Iterable[RuntimeContribution] = (),
    ) -> None:
        self._registry = registry
        registered_tool_names = {
            *_CONTROL_TOOL_CLASSES,
            _COMPATIBILITY_VERIFIER_TOOL_CLASS,
        }
        registered_tool_names.update(
            capability.tool_class_name for capability in registry.select("*", None)
        )
        plugin_ids: set[str] = set()
        for provider in plugin_providers:
            plugin_id = provider.plugin_id.strip()
            if not plugin_id or plugin_id in plugin_ids:
                raise ValueError("plugin_id must be non-empty and unique")
            plugin_ids.add(plugin_id)
            for name, definition in provider.tool_definitions().items():
                _register_tool_definition(name, definition, registered_tool_names)
            for capability in provider.capability_definitions():
                _register_capability(self._registry, capability)
        for contribution in runtime_contributions:
            for name, definition in contribution.tool_definitions.items():
                _register_tool_definition(name, definition, registered_tool_names)
            for capability in contribution.capabilities:
                _register_capability(self._registry, capability)

    def assemble(
        self,
        session_id: str,
        domain: str,
        evidence_types: Collection[str] | None,
        *,
        compatibility_restore: bool = False,
        repository: SessionEvidenceRepository | None = None,
        compatibility_mode: bool = False,
    ) -> list[Tool]:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        if repository is None and not compatibility_mode:
            raise RuntimeError("server-bound repository is required")
        params = {"session_id": session_id}
        tools = [Tool(name=name, params=dict(params)) for name in _CONTROL_TOOL_CLASSES]
        verifier_params: dict[str, Any] = dict(params)
        if repository is not None:
            verifier_params["repository"] = repository
        selected_evidence_types = None if compatibility_restore else evidence_types or None
        tools.extend(
            Tool(name=item.tool_class_name, params=dict(verifier_params))
            for item in self._registry.select(domain, selected_evidence_types)
        )
        if compatibility_restore and _COMPATIBILITY_VERIFIER_TOOL_CLASS not in {
            tool.name for tool in tools
        }:
            tools.append(
                Tool(
                    name=_COMPATIBILITY_VERIFIER_TOOL_CLASS,
                    params=dict(verifier_params),
                )
            )
        return tools

    def project_tool_names(
        self,
        domain: str,
        evidence_types: Collection[str] | None,
        *,
        compatibility_restore: bool = False,
    ) -> tuple[str, ...]:
        selected_types = None if compatibility_restore else evidence_types or None
        names = [*_CONTROL_TOOL_CLASSES]
        names.extend(
            item.tool_class_name for item in self._registry.select(domain, selected_types)
        )
        if compatibility_restore and _COMPATIBILITY_VERIFIER_TOOL_CLASS not in names:
            names.append(_COMPATIBILITY_VERIFIER_TOOL_CLASS)
        return tuple(names)

    def version(
        self,
        domain: str,
        evidence_types: Collection[str] | None,
        *,
        compatibility_restore: bool = False,
    ) -> str:
        selected_evidence_types = None if compatibility_restore else evidence_types or None
        selected = self._registry.select(domain, selected_evidence_types)
        has_compatibility_verifier = any(
            item.tool_class_name == _COMPATIBILITY_VERIFIER_TOOL_CLASS for item in selected
        )
        extra_identities = (
            ("repository-compatibility:2",)
            if compatibility_restore and not has_compatibility_verifier
            else ()
        )
        return toolset_version(selected, extra_identities=extra_identities)


def _register_tool_definition(
    name: str,
    definition: type[ToolDefinition[Any, Any]],
    registered_tool_names: set[str],
) -> None:
    normalized = name.strip()
    if not normalized:
        raise ValueError("tool name must not be empty")
    if normalized in registered_tool_names:
        raise ValueError(f"tool definition conflict: {normalized}")
    if normalized not in set(list_registered_tools()):
        register_tool(normalized, definition)
    registered_tool_names.add(normalized)


def _register_capability(
    registry: VerificationCapabilityRegistry,
    capability: VerificationCapability,
) -> None:
    try:
        registry.register(capability)
    except ValueError as exc:
        raise ValueError(f"capability conflict: {capability.registry_name}") from exc


def toolset_version(
    capabilities: Iterable[VerificationCapability],
    *,
    extra_identities: Iterable[str] = (),
) -> str:
    identities = [
        f"{item.registry_name}:{item.version}"
        for item in sorted(capabilities, key=lambda item: item.registry_name)
    ]
    identities.extend(sorted(extra_identities))
    identity = "\n".join(identities)
    return sha256(identity.encode("utf-8")).hexdigest()[:12]
