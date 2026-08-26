from __future__ import annotations

from dataclasses import dataclass


MiB = 1024 * 1024
MAX_MEDIA_ITEMS_PER_SESSION = 4
MAX_SOURCE_BYTES = 10 * MiB
MAX_CANONICAL_MESSAGE_BYTES = 10 * MiB
MAX_DISTINCT_NORMALIZED_BYTES = 20 * MiB


class MediaQuotaExceeded(ValueError):
    """Raised when media ingestion exceeds a product quota."""


@dataclass(frozen=True, slots=True)
class MediaLimits:
    max_items: int = MAX_MEDIA_ITEMS_PER_SESSION
    max_source_bytes: int = MAX_SOURCE_BYTES
    max_distinct_bytes: int = MAX_DISTINCT_NORMALIZED_BYTES


@dataclass(frozen=True, slots=True)
class SourceByteLimit:
    max_bytes: int = MAX_SOURCE_BYTES

    def check(self, byte_size: int) -> None:
        if byte_size > self.max_bytes:
            raise MediaQuotaExceeded(f"media source exceeds {self.max_bytes} bytes")


@dataclass(frozen=True, slots=True)
class CanonicalMessageByteLimit:
    max_bytes: int = MAX_CANONICAL_MESSAGE_BYTES

    def check(self, byte_size: int) -> None:
        if byte_size > self.max_bytes:
            raise MediaQuotaExceeded(
                f"canonical media message exceeds {self.max_bytes} bytes"
            )
