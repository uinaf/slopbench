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


def _events(value: object) -> tuple[Event, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("events must be a sequence")
    events = tuple(value)
    ids: set[str] = set()
    for event in events:
        if not isinstance(event, Event):
            raise ValueError("events must contain Event values")
        if not event.id or event.id in ids or not event.kind:
            raise ValueError("event IDs must be unique and text fields non-empty")
        if isinstance(event.created_at, bool) or not isinstance(event.created_at, int):
            raise ValueError("event time must be an integer")
        if event.created_at < 0:
            raise ValueError("event time must be non-negative")
        ids.add(event.id)
    return list_recent(events)


def _encode(event: Event, kind: str | None) -> str:
    raw = json.dumps(
        {"created_at": event.created_at, "id": event.id, "kind": kind},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(value: object, kind: str | None) -> tuple[int, str]:
    if not isinstance(value, str) or not value:
        raise ValueError("cursor must be a non-empty string")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"created_at", "id", "kind"}:
        raise ValueError("cursor is malformed")
    if payload["kind"] != kind:
        raise ValueError("cursor belongs to another filter")
    created_at, event_id = payload["created_at"], payload["id"]
    if isinstance(created_at, bool) or not isinstance(created_at, int) or created_at < 0:
        raise ValueError("cursor is malformed")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("cursor is malformed")
    return created_at, event_id


def paginate_events(
    events: Sequence[Event],
    *,
    limit: int,
    after: str | None = None,
    kind: str | None = None,
) -> EventPage:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be from 1 through 100")
    if kind is not None and (not isinstance(kind, str) or not kind):
        raise ValueError("kind must be a non-empty string")
    ordered = _events(events)
    start = 0
    if after is not None:
        position = _decode(after, kind)
        try:
            start = next(
                index + 1
                for index, event in enumerate(ordered)
                if (event.created_at, event.id) == position
            )
        except StopIteration as exc:
            raise ValueError("cursor does not identify an event") from exc
    window = ordered[start : start + limit]
    items = tuple(event for event in window if kind is None or event.kind == kind)
    more = start + len(window) < len(ordered)
    return EventPage(items, _encode(window[-1], kind) if window and more else None)
