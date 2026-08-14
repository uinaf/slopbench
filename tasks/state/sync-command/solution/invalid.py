from __future__ import annotations

from collections.abc import Mapping
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


def transition(
    state: SyncState,
    command: Mapping[str, object],
) -> tuple[SyncState, tuple[Effect, ...]]:
    kind = command.get("kind")
    if kind == "start":
        if state.status == "syncing":
            return state, ()
        generation = state.generation + 1
        return SyncState("syncing", generation), (Effect("upload", generation),)
    if kind == "succeeded":
        return SyncState("idle", int(command["generation"])), ()
    if kind == "failed":
        return SyncState("failed", int(command["generation"]), str(command["message"])), ()
    if kind == "cancel" and state.status == "syncing":
        return SyncState("idle", state.generation), (Effect("cancel", state.generation),)
    if kind == "cancel":
        return state, ()
    raise ValueError("unknown command kind")
