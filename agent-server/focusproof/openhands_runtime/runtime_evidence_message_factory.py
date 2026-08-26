from __future__ import annotations

import json
from typing import Literal, Protocol, TypeGuard


from focusproof.openhands_runtime.evidence_messages import FocusProofMessageEnvelope
from focusproof.persistence.repositories import MediaMessageArtifactFacts, StoredEvidence

MessageKind = Literal["goal", "evidence", "evidence_context", "answer"]


class RuntimeMediaContentProvider(Protocol):
    def get_facts(
        self,
        verified_user_id: str,
        session_id: str,
        evidence_id: str,
    ) -> MediaMessageArtifactFacts: ...


def is_media_evidence_record(record: StoredEvidence | None) -> TypeGuard[StoredEvidence]:
    return record is not None and record.evidence_type.startswith("image/")


def build_runtime_evidence_message(
    media_content_provider: RuntimeMediaContentProvider | None,
    *,
    verified_user_id: str,
    session_id: str,
    message_key: str,
    kind: MessageKind,
    payload: dict[str, object],
    record: StoredEvidence | None,
) -> str:
    serialized = serialize_message_envelope(
        schema_version=1,
        message_key=message_key,
        kind=kind,
        session_id=session_id,
        payload=payload,
    )
    if not is_media_evidence_record(record):
        return serialized

    if media_content_provider is None:
        raise RuntimeError("media message content provider is unavailable")
    facts = media_content_provider.get_facts(
        verified_user_id,
        session_id,
        record.evidence_id,
    )
    if facts.scan_result != "clean":
        raise RuntimeError("media message facts require a clean receipt")
    media_payload = dict(payload)
    media_payload.update(
        {
            "receipt_id": facts.receipt_id,
            "attempt_id": facts.attempt_id,
            "scan_result": facts.scan_result,
            "artifact_ref": facts.artifact_ref,
            "artifact_sha256": facts.artifact_sha256,
            "media_type": facts.media_type,
            "normalized_sha256": facts.normalized_sha256,
            "byte_size": facts.byte_size,
            "width": facts.width,
            "height": facts.height,
        }
    )
    return serialize_message_envelope(
        schema_version=1,
        message_key=message_key,
        kind=kind,
        session_id=session_id,
        payload=media_payload,
    )


def serialize_message_envelope(
    *,
    schema_version: Literal[1],
    message_key: str,
    kind: MessageKind,
    session_id: str,
    payload: dict[str, object],
) -> str:
    envelope = FocusProofMessageEnvelope(
        schema_version=schema_version,
        message_key=message_key,
        kind=kind,
        session_id=session_id,
        payload=payload,
    )
    return json.dumps(envelope.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
