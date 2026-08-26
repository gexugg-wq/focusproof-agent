from __future__ import annotations

from typing import Any, Literal

from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent

from focusproof.openhands_runtime.evidence_messages import FocusProofMessageEnvelope
from focusproof.openhands_runtime.runtime_evidence_message_factory import serialize_message_envelope
from focusproof.runtime.events import Event


_COMMON_SAFE_FIELDS = frozenset(
    {
        "runtimeSource",
        "sourceRuntime",
        "openhandsEventKind",
        "sourceIndex",
        "relatedEvidenceIds",
    }
)
_SAFE_FIELDS_BY_EVENT_TYPE = {
    "goal.submitted": _COMMON_SAFE_FIELDS
    | {"domain", "title", "goal", "expectedOutput", "plannedMinutes"},
    "evidence.submitted": _COMMON_SAFE_FIELDS
    | {
        "evidenceId",
        "evidenceType",
        "contentHash",
        "textContent",
        "sourceUrl",
        "receipt_id",
        "attempt_id",
        "scan_result",
        "artifact_ref",
        "artifact_sha256",
        "media_type",
        "normalized_sha256",
        "byte_size",
        "width",
        "height",
    },
    "answer.submitted": _COMMON_SAFE_FIELDS | {"questionId", "answer", "version"},
}


def _safe_event_payload(event: Event) -> dict[str, object]:
    allowed = _SAFE_FIELDS_BY_EVENT_TYPE.get(event.type, _COMMON_SAFE_FIELDS)
    return {
        key: value
        for key, value in event.payload.items()
        if key in allowed and not isinstance(value, bytes)
    }


def focusproof_event_to_openhands_message(
    event: Event,
    *,
    verified_sender: str,
) -> MessageEvent:
    if not isinstance(verified_sender, str) or not verified_sender.strip():
        raise ValueError("verified_sender must not be empty")
    kind: Literal["goal", "evidence", "answer"]
    if event.type == "evidence.submitted":
        kind = "evidence"
    elif event.type == "answer.submitted":
        kind = "answer"
    else:
        kind = "goal"
    payload = {
        "eventId": event.id,
        "eventType": event.type,
        "sessionId": event.sessionId,
        "sequence": event.sequence,
        "actor": event.actor,
    }
    payload.update(_safe_event_payload(event))
    text = serialize_message_envelope(
        schema_version=1,
        message_key=f"focusproof-event:{event.id}",
        kind=kind,
        session_id=event.sessionId,
        payload=payload,
    )
    source: Literal["user", "environment"] = "user" if event.actor == "user" else "environment"
    return MessageEvent(
        source=source,
        llm_message=Message(role="user", content=[TextContent(text=text)]),
        sender=verified_sender,
    )


def openhands_message_to_focusproof_payload(message: object) -> dict[str, Any]:
    if not isinstance(message, MessageEvent):
        raise TypeError("expected official OpenHands MessageEvent")
    text = "".join(
        content.text for content in message.llm_message.content if isinstance(content, TextContent)
    )
    envelope = FocusProofMessageEnvelope.model_validate_json(text)
    return dict(envelope.payload)
