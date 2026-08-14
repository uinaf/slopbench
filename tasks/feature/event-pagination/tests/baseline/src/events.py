from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    id: str
    created_at: int
    kind: str


def list_recent(events: Sequence[Event]) -> tuple[Event, ...]:
    return tuple(
        sorted(events, key=lambda event: (event.created_at, event.id), reverse=True)
    )
