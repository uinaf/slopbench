from __future__ import annotations

import base64
import json
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


def _ordered(events: object) -> list[Event]:
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise ValueError("invalid events")
    result = list(events)
    ids = [event.id for event in result if isinstance(event, Event)]
    if len(ids) != len(result) or len(ids) != len(set(ids)):
        raise ValueError("invalid events")
    if any(
        not event.id or not event.kind or type(event.created_at) is not int or event.created_at < 0
        for event in result
    ):
        raise ValueError("invalid events")
    result.sort(key=lambda event: (event.created_at, event.id), reverse=True)
    return result


def _cursor(position: tuple[int, str], kind: str | None) -> str:
    payload = [kind, position[0], position[1]]
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _position(cursor: object, kind: str | None) -> tuple[int, str]:
    if not isinstance(cursor, str) or not cursor:
        raise ValueError("invalid cursor")
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor))
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
    if (
        not isinstance(value, list)
        or len(value) != 3
        or value[0] != kind
        or type(value[1]) is not int
        or value[1] < 0
        or not isinstance(value[2], str)
        or not value[2]
    ):
        raise ValueError("invalid cursor")
    return value[1], value[2]


def paginate_events(
    events: Sequence[Event],
    *,
    limit: int,
    after: str | None = None,
    kind: str | None = None,
) -> EventPage:
    if type(limit) is not int or limit < 1 or limit > 100:
        raise ValueError("invalid limit")
    if kind is not None and (type(kind) is not str or kind == ""):
        raise ValueError("invalid kind")
    ordered = [event for event in _ordered(events) if kind is None or event.kind == kind]
    positions = [(event.created_at, event.id) for event in ordered]
    offset = 0
    if after is not None:
        try:
            offset = positions.index(_position(after, kind)) + 1
        except ValueError as exc:
            raise ValueError("unknown cursor") from exc
    items = tuple(ordered[offset : offset + limit])
    next_cursor = (
        _cursor(positions[offset + len(items) - 1], kind)
        if offset + len(items) < len(ordered)
        else None
    )
    return EventPage(items, next_cursor)
