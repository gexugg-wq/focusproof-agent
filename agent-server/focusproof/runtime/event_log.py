from __future__ import annotations

from collections import defaultdict
from typing import List
from uuid import uuid4

from focusproof.runtime.events import Actor, Event, EventType


class InMemoryEventLog:
    def __init__(self) -> None:
        self._events: dict[str, list[Event]] = defaultdict(list)

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
        if event_id is not None:
            existing = next(
                (event for event in self._events[session_id] if event.id == event_id),
                None,
            )
            if existing is not None:
                return existing.model_copy(deep=True)
        event = Event(
            id=event_id or f"evt_{uuid4().hex}",
            sessionId=session_id,
            type=event_type,
            sequence=self._next_sequence(session_id),
            actor=actor,
            payload=payload,
        )
        self._events[session_id].append(event.model_copy(deep=True))
        return event.model_copy(deep=True)

    def append_event(self, event: Event) -> Event:
        stored = event.model_copy(
            deep=True, update={"sequence": self._next_sequence(event.sessionId)}
        )
        self._events[event.sessionId].append(stored)
        return stored.model_copy(deep=True)

    def append_many(self, events: list[Event]) -> list[Event]:
        prepared: list[Event] = []
        next_sequences: dict[str, int] = {}
        for event in events:
            next_sequence = next_sequences.get(
                event.sessionId, self._next_sequence(event.sessionId)
            )
            prepared.append(event.model_copy(deep=True, update={"sequence": next_sequence}))
            next_sequences[event.sessionId] = next_sequence + 1
        for event in prepared:
            self._events[event.sessionId].append(event)
        return [event.model_copy(deep=True) for event in prepared]

    def list(self, session_id: str) -> list[Event]:
        return [event.model_copy(deep=True) for event in self._events[session_id]]

    def get_by_type(self, session_id: str, event_type: EventType) -> List[Event]:
        return [
            event.model_copy(deep=True)
            for event in self._events[session_id]
            if event.type == event_type
        ]

    def latest(self, session_id: str) -> Event | None:
        events = self._events[session_id]
        return events[-1].model_copy(deep=True) if events else None

    def count(self, session_id: str) -> int:
        return len(self._events[session_id])

    def has_source_event(self, session_id: str, source_event_id: str) -> bool:
        return any(
            event.payload.get("sourceOpenHandsEventId") == source_event_id
            for event in self._events[session_id]
        )

    def _next_sequence(self, session_id: str) -> int:
        return len(self._events[session_id]) + 1
