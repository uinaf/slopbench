from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class SyncState:
    status: str = "idle"
    generation: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class Effect:
    kind: str
    generation: int


def valid_generation(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("generation must be a non-negative integer")
    return value


def validate(state: object, command: object) -> tuple[SyncState, Mapping[str, object]]:
    if not isinstance(state, SyncState) or not isinstance(command, Mapping):
        raise ValueError("invalid transition input")
    valid_generation(state.generation)
    if state.status not in ("idle", "syncing", "failed"):
        raise ValueError("invalid state status")
    error_is_valid = (
        isinstance(state.last_error, str) and bool(state.last_error)
        if state.status == "failed"
        else state.last_error is None
    )
    if not error_is_valid:
        raise ValueError("state error invariant is invalid")
    return state, command


def transition(
    state: SyncState,
    command: Mapping[str, object],
) -> tuple[SyncState, tuple[Effect, ...]]:
    state, command = validate(state, command)
    match command.get("kind"):
        case "start" if state.status != "syncing":
            generation = state.generation + 1
            return SyncState("syncing", generation), (Effect("upload", generation),)
        case "start":
            return state, ()
        case "cancel" if state.status == "syncing":
            return SyncState("idle", state.generation), (Effect("cancel", state.generation),)
        case "cancel":
            return state, ()
        case "succeeded" | "failed" as kind:
            generation = valid_generation(command.get("generation"))
            message = command.get("message")
            if kind == "failed" and (not isinstance(message, str) or message == ""):
                raise ValueError("failed command requires a message")
            if state.status != "syncing" or state.generation != generation:
                return state, ()
            if kind == "succeeded":
                return replace(state, status="idle"), ()
            return replace(state, status="failed", last_error=message), ()
        case _:
            raise ValueError("unknown command kind")
