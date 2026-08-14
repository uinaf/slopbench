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


def _generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("generation must be a non-negative integer")
    return value


def _state(value: object) -> WatchState:
    if not isinstance(value, WatchState):
        raise ValueError("state must be a WatchState")
    _generation(value.generation)
    if value.status not in {"stopped", "connecting", "active"} or value.retry_count != 0:
        raise ValueError("invalid watch state")
    if value.status == "stopped":
        if value.topic is not None:
            raise ValueError("stopped state cannot carry a topic")
    elif not isinstance(value.topic, str) or not value.topic:
        raise ValueError("live state requires a topic")
    return value


def transition(
    state: WatchState,
    event: Mapping[str, object],
) -> tuple[WatchState, tuple[Effect, ...]]:
    current = _state(state)
    if not isinstance(event, Mapping):
        raise ValueError("event must be a mapping")
    kind = event.get("kind")
    if kind == "subscribe":
        topic = event.get("topic")
        if not isinstance(topic, str) or not topic:
            raise ValueError("subscribe requires a topic")
        if current.topic == topic:
            return current, ()
        generation = current.generation + 1
        effects = (
            ()
            if current.status == "stopped"
            else (Effect("disconnect", current.generation, current.topic),)
        )
        return WatchState("connecting", topic, generation, 0), (
            *effects,
            Effect("connect", generation, topic),
        )
    if kind == "connected":
        generation = _generation(event.get("generation"))
        if current.status != "connecting" or generation != current.generation:
            return current, ()
        return WatchState("active", current.topic, generation, 0), ()
    if kind == "unsubscribe":
        if current.status == "stopped":
            return current, ()
        return WatchState("stopped", None, current.generation, 0), (
            Effect("disconnect", current.generation, current.topic),
        )
    raise ValueError("unknown event kind")
