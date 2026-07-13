from __future__ import annotations

from threading import RLock
from typing import Any

from openhands.sdk.tool import ToolDefinition, list_registered_tools, register_tool

from focusproof.openhands_runtime.tools import SessionEvidenceRepository
from focusproof.openhands_runtime.tools.evidence_verification import (
    FocusProofEvidenceVerificationTool,
)
from focusproof.openhands_runtime.tools.learner_input import FocusProofLearnerInputTool
from focusproof.openhands_runtime.tools.review_draft import FocusProofReviewDraftTool

_LOCK = RLock()
_REPOSITORY_PROVIDER: SessionEvidenceRepository | None = None
_REGISTERED = False

_register_tool = register_tool

_TOOL_CLASSES: dict[str, type[ToolDefinition[Any, Any]]] = {
    "FocusProofEvidenceVerificationTool": FocusProofEvidenceVerificationTool,
    "FocusProofLearnerInputTool": FocusProofLearnerInputTool,
    "FocusProofReviewDraftTool": FocusProofReviewDraftTool,
}


def configure_repository_provider(provider: SessionEvidenceRepository) -> None:
    global _REPOSITORY_PROVIDER
    with _LOCK:
        _REPOSITORY_PROVIDER = provider


def get_repository_provider() -> SessionEvidenceRepository:
    with _LOCK:
        provider = _REPOSITORY_PROVIDER
    if provider is None:
        raise RuntimeError("FocusProof repository provider is not configured")
    return provider


def release_repository_provider() -> None:
    global _REPOSITORY_PROVIDER
    with _LOCK:
        _REPOSITORY_PROVIDER = None


def ensure_focusproof_tools_registered() -> None:
    global _REGISTERED
    with _LOCK:
        if _REGISTERED:
            return
        registered = set(list_registered_tools())
        for name, tool_class in _TOOL_CLASSES.items():
            if name not in registered:
                _register_tool(name, tool_class)
        _REGISTERED = True
