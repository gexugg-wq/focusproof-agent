from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from openhands.sdk.tool import ToolDefinition

from focusproof.domain.evidence_facts import (
    EvidenceAccessDenied as MediaEvidenceAccessDenied,
    EvidenceFactsNotReady as MediaEvidenceNotReady,
    MediaEvidenceFacts,
    ScopedMediaEvidenceRepository,
)
from focusproof.domain.scoring_inputs import (
    LearningNarrativeProjectionProvider,
    ReviewCompletionPolicy,
)
from focusproof.openhands_runtime.capabilities import VerificationCapability


__all__ = (
    "MediaEvidenceAccessDenied",
    "MediaEvidenceFacts",
    "MediaEvidenceNotReady",
    "RuntimeContribution",
    "ScopedMediaEvidenceRepository",
)


@dataclass(frozen=True, slots=True)
class RuntimeContribution:
    capabilities: tuple[VerificationCapability, ...]
    tool_definitions: Mapping[str, type[ToolDefinition[Any, Any]]]
    narrative_providers: tuple[LearningNarrativeProjectionProvider, ...] = ()
    completion_policies: tuple[ReviewCompletionPolicy, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(
            self,
            "tool_definitions",
            MappingProxyType(dict(self.tool_definitions)),
        )
        object.__setattr__(
            self,
            "narrative_providers",
            tuple(self.narrative_providers),
        )
        object.__setattr__(
            self,
            "completion_policies",
            tuple(self.completion_policies),
        )
