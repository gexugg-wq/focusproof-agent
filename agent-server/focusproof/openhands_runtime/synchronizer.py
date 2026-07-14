from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, cast

from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import TextContent

from focusproof.openhands_runtime.evidence_messages import (
    TEXT_EVIDENCE_CONTEXT_VERSION,
    runtime_evidence_payload,
)
from focusproof.openhands_runtime.handle import ConversationHandle
from focusproof.persistence.repositories import (
    StoredAnswer,
    StoredEvidence,
    StoredSession,
)
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike

MessageKind = Literal["goal", "evidence", "evidence_context", "answer"]


@dataclass(frozen=True)
class SyncResult:
    sent_count: int
    confirmed_count: int


@dataclass(frozen=True)
class _PendingMessage:
    key: str
    kind: MessageKind
    payload: dict[str, object]
    evidence_id: str | None = None
    question_id: str | None = None
    answer_version: int | None = None


class ConversationSynchronizer:
    def __init__(self, uow_factory: UnitOfWorkFactoryLike) -> None:
        self._uow_factory = uow_factory

    def sync(
        self,
        handle: ConversationHandle,
        *,
        verified_user_id: str,
    ) -> SyncResult:
        session, evidence, answers = self._load_product_facts(handle.session_id)
        if session.owner_user_id != verified_user_id:
            raise PermissionError("Verified identity does not own this session")

        existing_envelopes = {
            key: envelope
            for event in handle.conversation.state.events
            if isinstance(event, MessageEvent)
            if (envelope := message_envelope_from_event(event)) is not None
            if isinstance(key := envelope.get("message_key"), str)
        }
        existing_keys = set(existing_envelopes)
        sent_count = 0
        confirmed_count = 0
        for pending in _pending_messages(
            session,
            evidence,
            answers,
            existing_envelopes,
        ):
            if pending.key not in existing_keys:
                serialized = serialize_message_envelope(
                    schema_version=1,
                    message_key=pending.key,
                    kind=pending.kind,
                    session_id=session.session_id,
                    payload=pending.payload,
                )
                cast(Any, handle.conversation).send_message(
                    serialized,
                    sender=verified_user_id,
                )
                existing_keys.add(pending.key)
                sent_count += 1
            self._mark_confirmed(session.session_id, pending)
            confirmed_count += 1
        return SyncResult(sent_count=sent_count, confirmed_count=confirmed_count)

    def _load_product_facts(
        self, session_id: str
    ) -> tuple[StoredSession, list[StoredEvidence], list[StoredAnswer]]:
        with self._uow_factory() as uow:
            session = uow.sessions.get(session_id)
            if session is None:
                raise KeyError(f"Session {session_id} does not exist")
            evidence = uow.evidence.list_for_session(session_id)
            answers = uow.answers.list_for_session(session_id)
        return session, evidence, answers

    def _mark_confirmed(self, session_id: str, pending: _PendingMessage) -> None:
        from datetime import UTC, datetime

        if pending.kind == "evidence_context":
            return
        synced_at = datetime.now(UTC)
        with self._uow_factory() as uow:
            if pending.kind == "goal":
                uow.sessions.mark_goal_synced(session_id, synced_at)
            elif pending.kind == "evidence":
                assert pending.evidence_id is not None
                uow.evidence.mark_synced(session_id, pending.evidence_id, synced_at)
            else:
                assert pending.question_id is not None
                assert pending.answer_version is not None
                uow.answers.mark_synced(
                    session_id,
                    pending.question_id,
                    pending.answer_version,
                    synced_at,
                )
            uow.commit()


def serialize_message_envelope(
    *,
    schema_version: int,
    message_key: str,
    kind: MessageKind,
    session_id: str,
    payload: dict[str, object],
) -> str:
    return json.dumps(
        {
            "schema_version": schema_version,
            "message_key": message_key,
            "kind": kind,
            "session_id": session_id,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def message_key_from_event(event: MessageEvent) -> str | None:
    envelope = message_envelope_from_event(event)
    if envelope is None:
        return None
    message_key = envelope.get("message_key")
    return message_key if isinstance(message_key, str) else None


def message_envelope_from_event(event: MessageEvent) -> dict[str, Any] | None:
    if event.source != "user":
        return None
    text = "".join(
        content.text
        for content in event.llm_message.content
        if isinstance(content, TextContent)
    )
    try:
        envelope = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
        return None
    return envelope


def _pending_messages(
    session: StoredSession,
    evidence: list[StoredEvidence],
    answers: list[StoredAnswer],
    existing_envelopes: dict[str, dict[str, Any]] | None = None,
) -> list[_PendingMessage]:
    existing_envelopes = existing_envelopes or {}
    pending = [
        _PendingMessage(
            key=f"goal:{session.session_id}",
            kind="goal",
            payload={
                "domain": session.domain,
                "title": session.title,
                "goal": session.goal,
                "expectedOutput": session.expected_output,
                "plannedMinutes": session.planned_minutes,
            },
        )
    ]
    pending.extend(
        _PendingMessage(
            key=f"evidence:{record.evidence_id}",
            kind="evidence",
            payload=runtime_evidence_payload(
                {
                    "evidenceId": record.evidence_id,
                    "evidenceType": record.evidence_type,
                    "contentHash": record.content_hash,
                    "textContent": record.text_content,
                    "sourceUrl": record.source_url,
                }
            ),
            evidence_id=record.evidence_id,
        )
        for record in evidence
    )
    for record in evidence:
        if record.evidence_type != "text":
            continue
        evidence_key = f"evidence:{record.evidence_id}"
        existing = existing_envelopes.get(evidence_key)
        if existing is None or _has_text_context(existing):
            continue
        context_key = (
            f"evidence-context:{record.evidence_id}:"
            f"v{TEXT_EVIDENCE_CONTEXT_VERSION}"
        )
        if context_key in existing_envelopes:
            continue
        context_payload = runtime_evidence_payload(
            {
                "evidenceId": record.evidence_id,
                "evidenceType": record.evidence_type,
                "contentHash": record.content_hash,
                "textContent": record.text_content,
            }
        )
        pending.append(
            _PendingMessage(
                key=context_key,
                kind="evidence_context",
                payload=context_payload,
            )
        )
    pending.extend(
        _PendingMessage(
            key=(
                f"answer:{record.session_id}:{record.question_id}:{record.version}"
            ),
            kind="answer",
            payload={
                "questionId": record.question_id,
                "answer": record.answer,
                "version": record.version,
            },
            question_id=record.question_id,
            answer_version=record.version,
        )
        for record in answers
    )
    return pending


def _has_text_context(envelope: dict[str, Any]) -> bool:
    payload = envelope.get("payload")
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("textContent"), str)
        and payload.get("contentTrust") == "untrusted"
    )
