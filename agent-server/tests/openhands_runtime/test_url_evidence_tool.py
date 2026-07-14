from focusproof.openhands_runtime.tools.url_fetcher import FetchedUrl, UrlFetchError
from focusproof.openhands_runtime.tools.url_safety import UrlPolicyError
from focusproof.openhands_runtime.tools.verification import EvidenceReferenceAction
from focusproof.runtime.evidence import Evidence


class RecordingRepository:
    def __init__(self, stored: Evidence) -> None:
        self.stored = stored
        self.requested: list[tuple[str, str]] = []

    def get_evidence(self, session_id: str, evidence_id: str) -> Evidence:
        self.requested.append((session_id, evidence_id))
        if evidence_id != self.stored.evidenceId:
            raise KeyError(evidence_id)
        return self.stored


class FakeFetcher:
    def __init__(
        self,
        result: FetchedUrl | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.requested: list[str] = []

    def fetch(self, source_url: str) -> FetchedUrl:
        self.requested.append(source_url)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def evidence(
    evidence_id: str = "ev_url",
    evidence_type: str = "url",
    source_url: str | None = "https://example.com/guide",
) -> Evidence:
    return Evidence(
        evidenceId=evidence_id,
        evidenceType=evidence_type,
        contentHash=f"sha256:{evidence_id}",
        sourceUrl=source_url,
    )


def fetched() -> FetchedUrl:
    return FetchedUrl(
        final_url="https://example.com/guide",
        status_code=200,
        content_type="text/html",
        content_length=120,
        redirect_chain=("https://www.example.com/guide",),
        title="Guide",
        text_excerpt="A bounded guide excerpt.",
    )


def test_url_executor_reads_source_url_from_authoritative_repository() -> None:
    from focusproof.openhands_runtime.tools.url_evidence import (
        UrlEvidenceVerificationExecutor,
    )

    repository = RecordingRepository(evidence())
    fetcher = FakeFetcher(result=fetched())
    result = UrlEvidenceVerificationExecutor(repository, "sess_1", fetcher)(
        EvidenceReferenceAction(evidence_id="ev_url")
    )
    assert repository.requested == [("sess_1", "ev_url")]
    assert fetcher.requested == ["https://example.com/guide"]
    assert result.status == "success"
    assert result.facts == {
        "url": {
            "scheme": "https",
            "hostname": "example.com",
            "origin": "https://example.com",
            "path_redacted": True,
            "url_sha256": result.facts["url"]["url_sha256"],
        },
        "status_code": 200,
        "content_type": "text/html",
        "content_length": 120,
        "redirect_chain": [
            {
                "scheme": "https",
                "hostname": "www.example.com",
                "origin": "https://www.example.com",
                "path_redacted": True,
                "url_sha256": result.facts["redirect_chain"][0]["url_sha256"],
            }
        ],
        "title": "Guide",
        "text_excerpt": "A bounded [redacted] excerpt.",
    }
    assert len(result.facts["url"]["url_sha256"]) == 64
    assert result.source_refs == [
        "ev_url",
        "sha256:ev_url",
        f"url-sha256:{result.facts['url']['url_sha256']}",
    ]


def test_url_executor_maps_blocked_url_to_failed_observation() -> None:
    from focusproof.openhands_runtime.tools.url_evidence import (
        UrlEvidenceVerificationExecutor,
    )

    result = UrlEvidenceVerificationExecutor(
        RecordingRepository(
            evidence(source_url="https://user:secret@example.com/private")
        ),
        "sess_1",
        FakeFetcher(error=UrlPolicyError("url_address_blocked", "Blocked URL.")),
    )(EvidenceReferenceAction(evidence_id="ev_url"))
    assert result.status == "failed"
    assert result.error_code == "url_blocked"
    assert result.safe_error_message == "Blocked URL."
    assert result.source_refs == ["ev_url", "sha256:ev_url"]
    assert "secret" not in result.model_dump_json()


def test_url_executor_maps_dns_failure_to_inconclusive_network_error() -> None:
    from focusproof.openhands_runtime.tools.url_evidence import (
        UrlEvidenceVerificationExecutor,
    )

    result = UrlEvidenceVerificationExecutor(
        RecordingRepository(evidence()),
        "sess_1",
        FakeFetcher(
            error=UrlPolicyError(
                "dns_unavailable",
                "Hostname could not be resolved.",
            )
        ),
    )(EvidenceReferenceAction(evidence_id="ev_url"))
    assert result.status == "inconclusive"
    assert result.error_code == "network_unavailable"


def test_url_executor_maps_network_timeout_to_inconclusive() -> None:
    from focusproof.openhands_runtime.tools.url_evidence import (
        UrlEvidenceVerificationExecutor,
    )

    result = UrlEvidenceVerificationExecutor(
        RecordingRepository(evidence()),
        "sess_1",
        FakeFetcher(error=UrlFetchError("network_timeout", "Timed out.")),
    )(EvidenceReferenceAction(evidence_id="ev_url"))
    assert result.status == "inconclusive"
    assert result.error_code == "network_timeout"


def test_url_executor_maps_binary_content_to_unsupported() -> None:
    from focusproof.openhands_runtime.tools.url_evidence import (
        UrlEvidenceVerificationExecutor,
    )

    result = UrlEvidenceVerificationExecutor(
        RecordingRepository(evidence()),
        "sess_1",
        FakeFetcher(
            error=UrlFetchError(
                "content_type_unsupported",
                "Content type unsupported.",
            )
        ),
    )(EvidenceReferenceAction(evidence_id="ev_url"))
    assert result.status == "unsupported"
    assert result.error_code == "content_type_unsupported"


def test_url_observation_redacts_query_secrets_from_facts_and_sources() -> None:
    from focusproof.openhands_runtime.tools.url_evidence import (
        UrlEvidenceVerificationExecutor,
    )

    stored = evidence(source_url="https://example.com/guide?token=secret")
    fetched_url = fetched()
    fetched_url = FetchedUrl(
        final_url="https://example.com/guide?token=secret",
        status_code=fetched_url.status_code,
        content_type=fetched_url.content_type,
        content_length=fetched_url.content_length,
        redirect_chain=("https://example.com/next?key=secret",),
        title=fetched_url.title,
        text_excerpt=fetched_url.text_excerpt,
    )
    result = UrlEvidenceVerificationExecutor(
        RecordingRepository(stored),
        "sess_1",
        FakeFetcher(result=fetched_url),
    )(EvidenceReferenceAction(evidence_id="ev_url"))
    assert result.facts["url"]["origin"] == "https://example.com"
    assert result.facts["url"]["path_redacted"] is True
    assert result.facts["redirect_chain"][0]["origin"] == "https://example.com"
    assert result.source_refs == [
        "ev_url",
        "sha256:ev_url",
        f"url-sha256:{result.facts['url']['url_sha256']}",
    ]
    assert "secret" not in result.model_dump_json()


def test_url_observation_redacts_path_userinfo_port_redirect_and_excerpt_secrets() -> None:
    from focusproof.openhands_runtime.tools.url_evidence import (
        UrlEvidenceVerificationExecutor,
    )

    source_url = "https://example.com/hooks/secret-token?token=source-secret#source-fragment"
    fetched_url = FetchedUrl(
        final_url=(
            "https://user:password@example.com:8443/download/signed/abc123"
            "?token=final-secret#final-fragment"
        ),
        status_code=200,
        content_type="text/plain",
        content_length=80,
        redirect_chain=(
            "https://redirect-user:redirect-password@redirect.example:9443/"
            "private/redirect-secret?key=query-secret#redirect-fragment",
        ),
        title="Download abc123 for user",
        text_excerpt="Use secret-token and final-secret at redirect-secret.",
    )
    repository = RecordingRepository(evidence(source_url=source_url))
    fetcher = FakeFetcher(result=fetched_url)

    result = UrlEvidenceVerificationExecutor(repository, "sess_1", fetcher)(
        EvidenceReferenceAction(evidence_id="ev_url")
    )

    assert fetcher.requested == [source_url]
    assert result.facts["url"] == {
        "scheme": "https",
        "hostname": "example.com",
        "port": 8443,
        "origin": "https://example.com:8443",
        "path_redacted": True,
        "url_sha256": result.facts["url"]["url_sha256"],
    }
    assert result.facts["redirect_chain"][0]["port"] == 9443
    serialized = result.model_dump_json()
    for secret in (
        "secret-token",
        "source-secret",
        "source-fragment",
        "user",
        "password",
        "abc123",
        "final-secret",
        "final-fragment",
        "redirect-user",
        "redirect-password",
        "private",
        "redirect-secret",
        "query-secret",
        "redirect-fragment",
    ):
        assert secret not in serialized


def test_url_executor_rejects_missing_or_wrong_type_evidence_safely() -> None:
    from focusproof.openhands_runtime.tools.url_evidence import (
        UrlEvidenceVerificationExecutor,
    )

    repository = RecordingRepository(evidence(evidence_type="text", source_url=None))
    fetcher = FakeFetcher(result=fetched())
    unsupported = UrlEvidenceVerificationExecutor(repository, "sess_1", fetcher)(
        EvidenceReferenceAction(evidence_id="ev_url")
    )
    missing = UrlEvidenceVerificationExecutor(repository, "sess_1", fetcher)(
        EvidenceReferenceAction(evidence_id="ev_missing")
    )
    assert unsupported.status == "unsupported"
    assert unsupported.error_code == "evidence_type_unsupported"
    assert missing.status == "failed"
    assert missing.error_code == "evidence_not_found"
    assert fetcher.requested == []


def test_url_tool_is_read_only_and_accepts_only_evidence_id() -> None:
    from focusproof.openhands_runtime.tools.url_evidence import (
        FocusProofUrlEvidenceVerificationTool,
    )

    assert set(EvidenceReferenceAction.model_fields) == {"evidence_id"}
    annotations = FocusProofUrlEvidenceVerificationTool.annotations_for_focusproof()
    assert annotations.readOnlyHint is True
    assert annotations.destructiveHint is False
    assert annotations.idempotentHint is True
    assert annotations.openWorldHint is False
