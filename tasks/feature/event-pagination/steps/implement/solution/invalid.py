from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    id: str
    created_at: int
    kind: str


@dataclass(frozen=True)
class EventPage:
    items: tuple[Event, ...]
    next_cursor: str | None


def list_recent(events: Sequence[Event]) -> tuple[Event, ...]:
    return tuple(sorted(events, key=lambda event: (event.created_at, event.id), reverse=True))


def _ordered(events: object) -> tuple[Event, ...]:
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise ValueError("invalid events")
    result = tuple(events)
    ids = [event.id for event in result if isinstance(event, Event)]
    if len(ids) != len(result) or len(ids) != len(set(ids)):
        raise ValueError("invalid events")
    if any(not event.id or not event.kind or type(event.created_at) is not int for event in result):
        raise ValueError("invalid events")
    return list_recent(result)


def _encode(event: Event) -> str:
    return base64.urlsafe_b64encode(str(event.created_at).encode()).decode()


def _decode(cursor: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(cursor))
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid cursor") from exc


def paginate_events(events: Sequence[Event], *, limit: int, after: str | None = None) -> EventPage:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid limit")
    ordered = _ordered(events)
    start = 0
    if after is not None:
        created_at = _decode(after)
        try:
            start = next(i + 1 for i, event in enumerate(ordered) if event.created_at == created_at)
        except StopIteration as exc:
            raise ValueError("unknown cursor") from exc
    items = ordered[start : start + limit]
    return EventPage(items, _encode(items[-1]) if start + len(items) < len(ordered) else None)
