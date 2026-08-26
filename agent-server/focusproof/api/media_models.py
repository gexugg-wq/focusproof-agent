from __future__ import annotations

from pydantic import BaseModel


class MediaEvidenceResponse(BaseModel):
    evidenceId: str
    mediaType: str
    normalizedBytes: int
    replayed: bool
