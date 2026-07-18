from __future__ import annotations

from collections.abc import Collection, Iterable
from hashlib import sha256
from typing import Any

from openhands.sdk.tool import Tool

from focusproof.openhands_runtime.capabilities import (
    VerificationCapability,
    VerificationCapabilityRegistry,
)
from focusproof.openhands_runtime.tools import SessionEvidenceRepository

_CONTROL_TOOL_CLASSES = (
    "FocusProofLearnerInputTool",
    "FocusProofReviewDraftTool",
)
_LEGACY_VERIFIER_TOOL_CLASS = "FocusProofEvidenceVerificationTool"


class SessionToolAssembler:
    def __init__(self, registry: VerificationCapabilityRegistry) -> None:
        self._registry = registry

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
        if compatibility_restore and _LEGACY_VERIFIER_TOOL_CLASS not in {
            tool.name for tool in tools
        }:
            tools.append(
                Tool(
                    name=_LEGACY_VERIFIER_TOOL_CLASS,
                    params=dict(verifier_params),
                )
            )
        return tools

    def version(
        self,
        domain: str,
        evidence_types: Collection[str] | None,
        *,
        compatibility_restore: bool = False,
    ) -> str:
        selected_evidence_types = None if compatibility_restore else evidence_types or None
        selected = self._registry.select(domain, selected_evidence_types)
        has_legacy = any(
            item.tool_class_name == _LEGACY_VERIFIER_TOOL_CLASS for item in selected
        )
        extra_identities = (
            ("legacy:1",) if compatibility_restore and not has_legacy else ()
        )
        return toolset_version(
            selected,
            extra_identities=extra_identities,
        )


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
