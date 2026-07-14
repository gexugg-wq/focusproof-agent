from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, ClassVar, Protocol, Self

from openhands.sdk.tool import ToolAnnotations, ToolDefinition, ToolExecutor

from focusproof.openhands_runtime.tools import (
    SessionEvidenceRepository,
    read_only_annotations,
)
from focusproof.openhands_runtime.tools.url_fetcher import FetchedUrl, UrlFetchError
from focusproof.openhands_runtime.tools.url_safety import UrlPolicyError
from focusproof.openhands_runtime.url_redaction import redact_url, redact_url_text
from focusproof.openhands_runtime.tools.verification import (
    EvidenceReferenceAction,
    VerificationObservation,
    VerificationStatus,
    utc_now,
)

_VERIFIER_VERSION = "1"


class UrlFetcher(Protocol):
    def fetch(self, source_url: str) -> FetchedUrl: ...


class UrlEvidenceVerificationExecutor(
    ToolExecutor[EvidenceReferenceAction, VerificationObservation]
):
    def __init__(
        self,
        repository: SessionEvidenceRepository | None,
        session_id: str,
        fetcher: UrlFetcher | None,
    ) -> None:
        self._repository = repository
        self._session_id = session_id
        self._fetcher = fetcher

    def __call__(
        self,
        action: EvidenceReferenceAction,
        conversation: Any | None = None,
    ) -> VerificationObservation:
        del conversation
        started_at = utc_now()
        repository = self._repository
        if repository is None:
            from focusproof.openhands_runtime.tool_registry import (
                get_repository_provider,
            )

            repository = get_repository_provider()
        try:
            evidence = repository.get_evidence(self._session_id, action.evidence_id)
        except KeyError:
            return self._error_observation(
                action.evidence_id,
                status="failed",
                error_code="evidence_not_found",
                safe_message="Evidence was not found.",
                source_refs=[action.evidence_id],
                started_at=started_at,
            )

        source_refs = [evidence.evidenceId, evidence.contentHash]
        if evidence.evidenceType != "url":
            return self._error_observation(
                evidence.evidenceId,
                status="unsupported",
                error_code="evidence_type_unsupported",
                safe_message="The evidence type is not supported by this verifier.",
                source_refs=source_refs,
                started_at=started_at,
            )
        source_url = (evidence.sourceUrl or "").strip()
        if not source_url:
            return self._error_observation(
                evidence.evidenceId,
                status="failed",
                error_code="source_url_missing",
                safe_message="URL evidence does not contain a source URL.",
                source_refs=source_refs,
                started_at=started_at,
            )
        fetcher = self._fetcher
        if fetcher is None:
            from focusproof.openhands_runtime.tool_registry import (
                get_url_fetcher_provider,
            )

            fetcher = get_url_fetcher_provider()
        try:
            fetched = fetcher.fetch(source_url)
        except UrlPolicyError as exc:
            is_network_failure = exc.code == "dns_unavailable"
            return self._error_observation(
                evidence.evidenceId,
                status="inconclusive" if is_network_failure else "failed",
                error_code=(
                    "network_unavailable" if is_network_failure else "url_blocked"
                ),
                safe_message=exc.safe_message,
                source_refs=source_refs,
                started_at=started_at,
            )
        except UrlFetchError as exc:
            status: VerificationStatus
            if exc.code.startswith("network_"):
                status = "inconclusive"
            elif exc.code == "content_type_unsupported":
                status = "unsupported"
            else:
                status = "failed"
            return self._error_observation(
                evidence.evidenceId,
                status=status,
                error_code=exc.code,
                safe_message=exc.safe_message,
                source_refs=source_refs,
                started_at=started_at,
            )

        safe_final_url = redact_url(fetched.final_url)
        source_refs.append(f"url-sha256:{safe_final_url['url_sha256']}")
        external_urls = [source_url, fetched.final_url, *fetched.redirect_chain]
        facts = {
            "url": safe_final_url,
            "status_code": fetched.status_code,
            "content_type": fetched.content_type,
            "content_length": fetched.content_length,
            "redirect_chain": [redact_url(url) for url in fetched.redirect_chain],
            "title": redact_url_text(fetched.title, external_urls),
            "text_excerpt": redact_url_text(fetched.text_excerpt, external_urls),
        }
        payload = {
            "evidence_id": evidence.evidenceId,
            "capability": "url",
            "status": "success",
            "facts": facts,
            "source_refs": source_refs,
            "verifier_version": _VERIFIER_VERSION,
        }
        return VerificationObservation.from_text(
            json.dumps(payload, sort_keys=True),
            evidence_id=evidence.evidenceId,
            capability="url",
            status="success",
            facts=facts,
            weak_signals=[],
            source_refs=source_refs,
            verifier_version=_VERIFIER_VERSION,
            started_at=started_at,
            completed_at=utc_now(),
        )
    @staticmethod
    def _error_observation(
        evidence_id: str,
        *,
        status: VerificationStatus,
        error_code: str,
        safe_message: str,
        source_refs: list[str],
        started_at: datetime,
    ) -> VerificationObservation:
        return VerificationObservation.from_text(
            safe_message,
            evidence_id=evidence_id,
            capability="url",
            status=status,
            facts={},
            weak_signals=[],
            source_refs=source_refs,
            verifier_version=_VERIFIER_VERSION,
            started_at=started_at,
            completed_at=utc_now(),
            error_code=error_code,
            safe_error_message=safe_message,
        )


class FocusProofUrlEvidenceVerificationTool(
    ToolDefinition[EvidenceReferenceAction, VerificationObservation]
):
    name: ClassVar[str] = "focusproof_url_evidence_verification"

    @classmethod
    def annotations_for_focusproof(cls) -> ToolAnnotations:
        return read_only_annotations("FocusProof URL evidence verification")

    @classmethod
    def create(
        cls,
        conv_state: Any | None = None,
        *,
        session_id: str,
        repository: SessionEvidenceRepository | None = None,
        fetcher: UrlFetcher | None = None,
    ) -> Sequence[Self]:
        del conv_state
        return [
            cls(
                description=(
                    "Inspect authoritative URL evidence loaded by evidence_id. "
                    "Only evidence_id is accepted; never provide a URL or evidence body."
                ),
                action_type=EvidenceReferenceAction,
                observation_type=VerificationObservation,
                executor=UrlEvidenceVerificationExecutor(
                    repository,
                    session_id,
                    fetcher,
                ),
                annotations=cls.annotations_for_focusproof(),
            )
        ]
