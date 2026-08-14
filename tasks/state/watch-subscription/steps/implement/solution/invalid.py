from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


def is_active(status: str) -> bool:
    return status == "active"


@dataclass(frozen=True)
class WatchState:
    status: str = "stopped"
    topic: str | None = None
    generation: int = 0
    retry_count: int = 0


@dataclass(frozen=True)
class Effect:
    kind: str
    generation: int
    topic: str
    delay_seconds: int | None = None


def transition(
    state: WatchState,
    event: Mapping[str, object],
) -> tuple[WatchState, tuple[Effect, ...]]:
    kind = event.get("kind")
    if kind == "subscribe":
        topic = event["topic"]
        if state.topic == topic:
            return state, ()
        generation = state.generation + 1
        effects = []
        if state.status != "stopped":
            effects.append(Effect("disconnect", state.generation, state.topic))
        effects.append(Effect("connect", generation, topic))
        return WatchState("connecting", topic, generation, 0), tuple(effects)
    if kind == "connected":
        return WatchState("active", state.topic, int(event["generation"]), 0), ()
    if kind == "unsubscribe" and state.status != "stopped":
        return WatchState("stopped", None, state.generation, 0), (
            Effect("disconnect", state.generation, state.topic),
        )
    if kind == "unsubscribe":
        return state, ()
    raise ValueError("unknown event kind")
