from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SyncState:
    status: str = "idle"
    generation: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class Effect:
    kind: str
    generation: int
