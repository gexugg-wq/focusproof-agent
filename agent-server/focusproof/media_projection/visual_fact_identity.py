from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

VisualFactType = Literal["visual_text"]
VISUAL_FACT_CAPABILITY = "image"
VISUAL_FACT_TOOL_NAME = "focusproof_media_evidence_verification"


@dataclass(frozen=True, slots=True)
class VisualFactIdentity:
    index: int
    normalized_text: str
    fact_id: str
    text_digest: str
    fact_type: VisualFactType = "visual_text"


def normalize_visual_fact_texts(visual_facts: object) -> tuple[str, ...]:
    """Return non-empty visual fact text with whitespace normalized in source order."""

    if not isinstance(visual_facts, Sequence) or isinstance(visual_facts, (str, bytes, bytearray)):
        return ()
    return tuple(
        " ".join(item.split()) for item in visual_facts if isinstance(item, str) and item.strip()
    )


def derive_visual_fact_identities(
    evidence_id: str,
    source_observation_event_id: str,
    visual_facts: object,
) -> tuple[VisualFactIdentity, ...]:
    """Normalize ordered visual facts and derive their stable product identities."""

    if (
        not isinstance(evidence_id, str)
        or not evidence_id
        or evidence_id != evidence_id.strip()
        or not isinstance(source_observation_event_id, str)
        or not source_observation_event_id
        or source_observation_event_id != source_observation_event_id.strip()
    ):
        raise ValueError("visual fact identity anchors must be strict identifiers")
    normalized = normalize_visual_fact_texts(visual_facts)
    return tuple(
        VisualFactIdentity(
            index=index,
            normalized_text=text,
            fact_id="fact_"
            + sha256(
                f"{evidence_id}\n{source_observation_event_id}\n{index}\n{text}".encode()
            ).hexdigest(),
            text_digest=sha256(text.encode()).hexdigest(),
        )
        for index, text in enumerate(normalized)
    )


def derive_visual_projection_id(
    evidence_id: str,
    source_observation_event_id: str,
    fact_ids: Sequence[str],
) -> str:
    """Derive the projection identity from ordered independently derived fact IDs."""

    if any(not isinstance(fact_id, str) or not fact_id for fact_id in fact_ids):
        raise ValueError("visual projection fact IDs must be non-empty strings")
    return (
        "projection_"
        + sha256(
            f"{evidence_id}\n{source_observation_event_id}\n".encode()
            + "\n".join(fact_ids).encode()
        ).hexdigest()
    )
