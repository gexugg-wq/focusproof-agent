from __future__ import annotations

from typing import Protocol

from openhands.sdk.tool.tool import ToolAnnotations

from focusproof.runtime.evidence import Evidence


class SessionEvidenceRepository(Protocol):
    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence: ...


def read_only_annotations(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
