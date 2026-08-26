from __future__ import annotations

from focusproof.media_core.models import MediaReservationStatus


def test_crash_state_names_match_compensated_media_lifecycle() -> None:
    states: tuple[MediaReservationStatus, ...] = (
        "LEASED",
        "RECEIVING",
        "QUARANTINED",
        "VALIDATED",
        "NORMALIZED",
        "STAGED",
        "REFERENCED",
        "REJECTED",
        "ABORTED",
        "EXPIRED",
    )

    assert states == (
        "LEASED",
        "RECEIVING",
        "QUARANTINED",
        "VALIDATED",
        "NORMALIZED",
        "STAGED",
        "REFERENCED",
        "REJECTED",
        "ABORTED",
        "EXPIRED",
    )
