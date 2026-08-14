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
        generation = state.generation + 1
        return WatchState("connecting", topic, generation, 0), (
            Effect("connect", generation, topic),
        )
    if kind == "connected":
        return WatchState("active", state.topic, int(event["generation"]), 0), ()
    if kind == "connection_lost":
        generation = int(event["generation"])
        return WatchState("waiting", state.topic, generation, state.retry_count + 1), (
            Effect("schedule_retry", generation, state.topic, 1),
        )
    if kind == "retry_due":
        return WatchState("connecting", state.topic, state.generation, state.retry_count), (
            Effect("connect", state.generation, state.topic),
        )
    if kind == "unsubscribe":
        return WatchState("stopped", None, state.generation, 0), (
            Effect("disconnect", state.generation, state.topic),
        )
    raise ValueError("unknown event kind")
