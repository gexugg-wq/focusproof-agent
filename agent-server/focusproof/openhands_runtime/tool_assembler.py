from __future__ import annotations

from collections.abc import Collection, Iterable
from hashlib import sha256

from openhands.sdk.tool import Tool

from focusproof.openhands_runtime.capabilities import (
    VerificationCapability,
    VerificationCapabilityRegistry,
)

_CONTROL_TOOL_CLASSES = (
    "FocusProofLearnerInputTool",
    "FocusProofReviewDraftTool",
)


class SessionToolAssembler:
    def __init__(self, registry: VerificationCapabilityRegistry) -> None:
        self._registry = registry

    def assemble(
        self,
        session_id: str,
        domain: str,
        evidence_types: Collection[str] | None,
    ) -> list[Tool]:
        if not session_id.strip():
            raise ValueError("session_id must not be empty")
        params = {"session_id": session_id}
        tools = [Tool(name=name, params=dict(params)) for name in _CONTROL_TOOL_CLASSES]
        tools.extend(
            Tool(name=item.tool_class_name, params=dict(params))
            for item in self._registry.select(domain, evidence_types)
        )
        return tools

    def version(
        self,
        domain: str,
        evidence_types: Collection[str] | None,
    ) -> str:
        return toolset_version(self._registry.select(domain, evidence_types))


def toolset_version(capabilities: Iterable[VerificationCapability]) -> str:
    identity = "\n".join(
        f"{item.registry_name}:{item.version}"
        for item in sorted(capabilities, key=lambda item: item.registry_name)
    )
    return sha256(identity.encode("utf-8")).hexdigest()[:12]
