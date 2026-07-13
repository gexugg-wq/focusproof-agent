from __future__ import annotations

from typing import Any

from focusproof.runtime.events import Event


def focusproof_event_to_openhands_message(event: Event) -> dict[str, Any]:
    return {
        "source": "focusproof",
        "eventType": event.type,
        "sessionId": event.sessionId,
        "sequence": event.sequence,
        "payload": event.payload,
    }


def openhands_message_to_focusproof_payload(message: object) -> dict[str, Any]:
    return {"rawOpenHandsPayload": repr(message), "conversion": "raw-preserved"}
