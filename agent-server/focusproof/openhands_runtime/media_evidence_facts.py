from __future__ import annotations

from focusproof.domain.evidence_facts import (
    EvidenceAccessDenied,
    EvidenceFactsNotReady,
    LegacyScanProjection,
    MediaEvidenceFacts,
    normalize_legacy_scan_projection,
    ScopedMediaEvidenceRepository,
)


MediaEvidenceAccessDenied = EvidenceAccessDenied
MediaEvidenceNotReady = EvidenceFactsNotReady


__all__ = (
    "MediaEvidenceAccessDenied",
    "LegacyScanProjection",
    "MediaEvidenceFacts",
    "normalize_legacy_scan_projection",
    "MediaEvidenceNotReady",
    "ScopedMediaEvidenceRepository",
)
