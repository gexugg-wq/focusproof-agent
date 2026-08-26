from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
from typing import BinaryIO, Iterator, cast
from typing import TYPE_CHECKING
from focusproof.media_core.ingestion import CleanupError, MediaIngestionService
from focusproof.media_core.limits import MediaQuotaExceeded
from focusproof.config.profiles import (
    MediaSecurityPolicy,
    load_media_security_policy,
)
from focusproof.media_application import (
    MediaValidationBoundary,
    MediaDisabledError,
    MediaSourceTooLargeError,
    map_malware_scan_error,
)
from focusproof.media_core.ports import (
    MediaCancellationGate,
    MalwareScanner,
    MediaNormalizer,
    MediaUnitOfWorkFactory,
)
from focusproof.media_core.models import IngestedEvidenceResult, MediaReservationRequest
from focusproof.persistence.unit_of_work import UnitOfWorkFactory


_CHUNK_SIZE = 1024 * 1024
LOGGER = logging.getLogger("focusproof.media")


if TYPE_CHECKING:
    from focusproof.media_adapters.media_message_content import MediaMessageContentProvider
    from focusproof.openhands_runtime.locks import SessionRunLock
    from focusproof.openhands_runtime.runtime_contributions import RuntimeContribution


@dataclass(frozen=True, slots=True)
class MediaCommandOutcome:
    result: IngestedEvidenceResult
    replayed: bool


class ImageEvidenceCommand:
    def __init__(self, service: MediaIngestionService) -> None:
        self._service = service

    def execute(
        self,
        *,
        owner_id: str,
        session_id: str,
        stream: BinaryIO,
        declared_media_type: str | None,
        explanation: str,
        idempotency_key: str,
        cancellation_gate: MediaCancellationGate | None = None,
    ) -> MediaCommandOutcome:
        source_digest = sha256()
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            source_digest.update(chunk)
        stream.seek(0)
        fingerprint = sha256(
            json.dumps(
                {
                    "media_type": declared_media_type,
                    "explanation": explanation,
                    "source_sha256": source_digest.hexdigest(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        request = MediaReservationRequest(
            owner_id=owner_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        try:
            result = self._service.ingest(
                request=request,
                chunks=_chunks(stream),
                declared_media_type=declared_media_type,
                learner_explanation=explanation,
                cancellation_gate=cancellation_gate,
            )
        except MediaQuotaExceeded as exc:
            if str(exc).startswith("media source exceeds"):
                raise MediaSourceTooLargeError("media source too large") from exc
            raise
        except Exception as exc:
            mapped = map_malware_scan_error(exc)
            if mapped is not exc:
                raise mapped from None
            raise
        return MediaCommandOutcome(result=result, replayed=stream.tell() == 0)


class DisabledMediaCommand:
    def execute(self, **kwargs: object) -> object:
        del kwargs
        raise MediaDisabledError("media upload is disabled")


def build_malware_scanner(policy: MediaSecurityPolicy) -> MalwareScanner:
    if policy.mode == "clamd":
        from focusproof.media_adapters.clamd_malware_scanner import ClamdMalwareScanner
        from focusproof.media_adapters.clamd_limits import ClamdLimits

        assert policy.endpoint is not None
        return ClamdMalwareScanner(
            endpoint=policy.endpoint,
            limits=ClamdLimits(
                max_bytes=policy.max_scan_bytes,
                max_concurrent_scans=policy.max_concurrent_scans,
                deadline_ms=max(1, round(policy.total_timeout_seconds * 1000)),
                socket_timeout_ms=max(1, round(policy.connect_timeout_seconds * 1000)),
                admission_timeout_ms=max(1, round(policy.admission_timeout_seconds * 1000)),
                definitions_version=policy.definitions_version,
                definitions_fresh_at=policy.definitions_fresh_at,
            ),
        )
    if policy.mode.startswith("fake-"):
        from focusproof.media_adapters.fake_malware_scanner import FakeMalwareScanner

        return FakeMalwareScanner.from_mode(policy.mode)
    raise ValueError("disabled media has no scanner")


def _chunks(stream: BinaryIO) -> Iterator[bytes]:
    for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
        yield chunk


def compose_media_command(
    *,
    uow_factory: UnitOfWorkFactory,
    data_dir: Path,
    security_policy: MediaSecurityPolicy | None = None,
    session_run_lock: SessionRunLock | None = None,
) -> ImageEvidenceCommand | DisabledMediaCommand:
    policy = security_policy or load_media_security_policy(
        os.environ.get("FOCUSPROOF_PROFILE", "local-dev"),  # type: ignore[arg-type]
        os.environ,
    )
    if not policy.upload_enabled:
        return DisabledMediaCommand()
    malware_scanner = build_malware_scanner(policy)

    from focusproof.media_adapters.local_media_object_store import LocalMediaObjectStore
    from focusproof.media_adapters.local_quarantine_store import LocalQuarantineStore
    from focusproof.media_adapters.pillow_image_codec import PillowImageCodecAdapter

    codec = PillowImageCodecAdapter()
    service = MediaIngestionService(
        uow_factory=cast(MediaUnitOfWorkFactory, uow_factory),
        quarantine_store=LocalQuarantineStore(data_dir / "media" / "quarantine"),
        malware_scanner=malware_scanner,
        validator=MediaValidationBoundary(codec),
        normalizer=cast(MediaNormalizer, codec),
        object_store=LocalMediaObjectStore(data_dir / "media" / "objects"),
        cleanup_error_reporter=_report_cleanup_error,
        session_lock_acquire=(None if session_run_lock is None else session_run_lock.acquire),
    )
    return ImageEvidenceCommand(service)


def compose_media_message_content_provider(
    *,
    uow_factory: UnitOfWorkFactory,
    data_dir: Path,
) -> MediaMessageContentProvider:
    from focusproof.media_adapters.local_media_object_store import LocalMediaObjectStore
    from focusproof.media_adapters.media_message_content import MediaMessageContentProvider

    from focusproof.media_adapters.pillow_image_codec import PillowImageCodecAdapter

    return MediaMessageContentProvider(
        uow_factory,
        LocalMediaObjectStore(data_dir / "media" / "objects"),
        image_validator=PillowImageCodecAdapter(),
    )


def compose_media_runtime_contribution(repository: object) -> RuntimeContribution:
    if repository is None:
        raise ValueError("media repository is required")
    from focusproof.media_projection.image_narrative_provider import (
        ImageNarrativeProvider,
        ImageVerificationCompletionPolicy,
    )
    from focusproof.openhands_runtime.capabilities import VerificationCapability
    from focusproof.openhands_runtime.runtime_contributions import RuntimeContribution
    from focusproof.openhands_runtime.tools.media_evidence import (
        FocusProofMediaEvidenceVerificationTool,
    )

    return RuntimeContribution(
        capabilities=(
            VerificationCapability(
                registry_name="image",
                tool_class_name="FocusProofMediaEvidenceVerificationTool",
                supported_evidence_types=frozenset({"image/png", "image/jpeg", "image/webp"}),
                supported_domains=frozenset({"*"}),
                priority=30,
                read_only=True,
                requires_network=False,
                timeout_seconds=5.0,
                enabled=True,
                version="1",
            ),
        ),
        tool_definitions={
            "FocusProofMediaEvidenceVerificationTool": FocusProofMediaEvidenceVerificationTool
        },
        narrative_providers=(ImageNarrativeProvider(),),
        completion_policies=(ImageVerificationCompletionPolicy(),),
    )


def compose_optional_media_runtime_contribution(
    *,
    enabled: bool,
    repository: object,
) -> RuntimeContribution | None:
    if not enabled:
        return None
    return compose_media_runtime_contribution(repository)


def _report_cleanup_error(error: CleanupError) -> None:
    LOGGER.error("media cleanup failed", extra={"error_count": len(error.errors)})
