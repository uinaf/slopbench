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


def _generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("generation must be a non-negative integer")
    return value


def _state(state: object) -> SyncState:
    if not isinstance(state, SyncState):
        raise ValueError("state must be a SyncState")
    _generation(state.generation)
    if state.status not in {"idle", "syncing", "failed"}:
        raise ValueError("unknown sync status")
    if state.status == "failed":
        if not isinstance(state.last_error, str) or not state.last_error:
            raise ValueError("failed state requires an error")
    elif state.last_error is not None:
        raise ValueError("only failed state may carry an error")
    return state


def transition(
    state: SyncState,
    command: Mapping[str, object],
) -> tuple[SyncState, tuple[Effect, ...]]:
    current = _state(state)
    if not isinstance(command, Mapping):
        raise ValueError("command must be a mapping")
    kind = command.get("kind")
    if kind == "start":
        if current.status == "syncing":
            return current, ()
        generation = current.generation + 1
        return SyncState("syncing", generation), (Effect("upload", generation),)
    if kind == "cancel":
        if current.status != "syncing":
            return current, ()
        return SyncState("idle", current.generation), (Effect("cancel", current.generation),)
    if kind in {"succeeded", "failed"}:
        generation = _generation(command.get("generation"))
        message = command.get("message")
        if kind == "failed" and (not isinstance(message, str) or not message):
            raise ValueError("failed command requires a non-empty message")
        if current.status != "syncing" or generation != current.generation:
            return current, ()
        if kind == "succeeded":
            return SyncState("idle", generation), ()
        return SyncState("failed", generation, message), ()
    raise ValueError("unknown command kind")
