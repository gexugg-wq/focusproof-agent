from __future__ import annotations

import builtins
from typing import cast

from focusproof.persistence.repositories import StoredAuditEvent
from focusproof.persistence.unit_of_work import UnitOfWorkFactoryLike
from focusproof.runtime.events import Actor, Event, EventType


class PersistentAuditEventLog:
    def __init__(self, uow_factory: UnitOfWorkFactoryLike) -> None:
        self._uow_factory = uow_factory

    def append(
        self,
        session_id: str,
        event_type: EventType,
        actor: Actor,
        payload: dict[str, object],
    ) -> Event:
        return self._append(session_id, event_type, actor, payload)

    def append_final(
        self,
        session_id: str,
        event_type: EventType,
        actor: Actor,
        payload: dict[str, object],
        *,
        event_id: str,
    ) -> Event:
        return self._append(session_id, event_type, actor, payload, event_id=event_id)

    def _append(
        self,
        session_id: str,
        event_type: EventType,
        actor: Actor,
        payload: dict[str, object],
        *,
        event_id: str | None = None,
    ) -> Event:
        source_id = payload.get("sourceOpenHandsEventId")
        with self._uow_factory() as uow:
            stored = uow.audit_events.append(
                session_id,
                event_type,
                actor,
                dict(payload),
                source_openhands_event_id=(source_id if isinstance(source_id, str) else None),
                event_id=event_id,
            )
            uow.commit()
        return _runtime_event(stored)

    def list(self, session_id: str) -> builtins.list[Event]:
        with self._uow_factory() as uow:
            return [_runtime_event(event) for event in uow.audit_events.list(session_id)]

    def latest(self, session_id: str) -> Event | None:
        with self._uow_factory() as uow:
            event = uow.audit_events.latest(session_id)
        return _runtime_event(event) if event is not None else None

    def has_source_event(self, session_id: str, source_event_id: str) -> bool:
        with self._uow_factory() as uow:
            return uow.audit_events.has_source_event(session_id, source_event_id)

    def get_by_type(
        self, session_id: str, event_type: EventType
    ) -> builtins.list[Event]:
        return [event for event in self.list(session_id) if event.type == event_type]

    def count(self, session_id: str) -> int:
        return len(self.list(session_id))


def _runtime_event(stored: StoredAuditEvent) -> Event:
    return Event(
        id=stored.event_id,
        sessionId=stored.session_id,
        type=cast(EventType, stored.type),
        sequence=stored.sequence,
        createdAt=stored.created_at.isoformat(),
        actor=cast(Actor, stored.actor),
        payload=stored.payload,
    )
