from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from openhands.sdk.utils.redact import redact_text_secrets
from pydantic import BaseModel, ConfigDict

from focusproof.openhands_runtime.url_redaction import safe_evidence_payload


class FocusProofMessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    message_key: str
    kind: Literal["goal", "evidence", "evidence_context", "answer"]
    session_id: str
    payload: dict[str, object]

MAX_TEXT_EVIDENCE_CHARACTERS = 4_000
TEXT_EVIDENCE_CONTEXT_VERSION = 1


def runtime_evidence_payload(evidence: Mapping[str, Any]) -> dict[str, object]:
    """Build bounded, privacy-aware evidence context for a native user message.

    Audit projection remains the responsibility of ``safe_evidence_payload``.
    Text is explicitly untrusted learner-supplied context, never an instruction.
    URL evidence retains only the already-approved origin/hash representation.
    """
    payload = safe_evidence_payload(evidence)
    if payload.get("evidenceType") != "text":
        return payload

    raw_text = evidence.get("textContent", evidence.get("text_content"))
    text = raw_text if isinstance(raw_text, str) else ""
    redacted_text = redact_text_secrets(text)
    payload.update(
        {
            "contentTrust": "untrusted",
            "contextSchemaVersion": TEXT_EVIDENCE_CONTEXT_VERSION,
            "textContent": redacted_text[:MAX_TEXT_EVIDENCE_CHARACTERS],
            "textTruncated": (
                len(text) > MAX_TEXT_EVIDENCE_CHARACTERS
                or len(redacted_text) > MAX_TEXT_EVIDENCE_CHARACTERS
            ),
            "originalCharacterCount": len(text),
        }
    )
    return payload
