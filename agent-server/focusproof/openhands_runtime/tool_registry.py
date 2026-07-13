from __future__ import annotations

from threading import RLock
from collections.abc import Callable
from typing import Any

from openhands.sdk.tool import ToolDefinition, list_registered_tools, register_tool

from focusproof.openhands_runtime.tools import SessionEvidenceRepository
from focusproof.openhands_runtime.tools.evidence_verification import (
    FocusProofEvidenceVerificationTool,
)
from focusproof.openhands_runtime.tools.learner_input import FocusProofLearnerInputTool
from focusproof.openhands_runtime.tools.review_draft import FocusProofReviewDraftTool
from focusproof.openhands_runtime.tools.text_evidence import (
    FocusProofTextEvidenceVerificationTool,
)
from focusproof.openhands_runtime.tools.url_evidence import (
    FocusProofUrlEvidenceVerificationTool,
    UrlFetcher,
)

_LOCK = RLock()
_REPOSITORY_PROVIDER: SessionEvidenceRepository | None = None
_URL_FETCHER_PROVIDER: UrlFetcher | None = None
_URL_FETCHER_CLOSER: Callable[[], None] | None = None
_REGISTERED = False

_register_tool = register_tool

_TOOL_CLASSES: dict[str, type[ToolDefinition[Any, Any]]] = {
    "FocusProofEvidenceVerificationTool": FocusProofEvidenceVerificationTool,
    "FocusProofLearnerInputTool": FocusProofLearnerInputTool,
    "FocusProofReviewDraftTool": FocusProofReviewDraftTool,
    "FocusProofTextEvidenceVerificationTool": FocusProofTextEvidenceVerificationTool,
    "FocusProofUrlEvidenceVerificationTool": FocusProofUrlEvidenceVerificationTool,
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


def configure_url_fetcher_provider(
    provider: UrlFetcher,
    *,
    close: Callable[[], None] | None = None,
) -> None:
    global _URL_FETCHER_PROVIDER, _URL_FETCHER_CLOSER
    with _LOCK:
        previous_closer = _URL_FETCHER_CLOSER
        _URL_FETCHER_PROVIDER = provider
        _URL_FETCHER_CLOSER = close
    if previous_closer is not None:
        previous_closer()


def get_url_fetcher_provider() -> UrlFetcher:
    with _LOCK:
        provider = _URL_FETCHER_PROVIDER
    if provider is None:
        raise RuntimeError("FocusProof URL fetcher provider is not configured")
    return provider


def release_repository_provider() -> None:
    global _REPOSITORY_PROVIDER, _URL_FETCHER_PROVIDER, _URL_FETCHER_CLOSER
    with _LOCK:
        closer = _URL_FETCHER_CLOSER
        _REPOSITORY_PROVIDER = None
        _URL_FETCHER_PROVIDER = None
        _URL_FETCHER_CLOSER = None
    if closer is not None:
        closer()


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
