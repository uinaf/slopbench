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


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("generation and retry_count must be non-negative integers")
    return value


def _state(value: object) -> WatchState:
    if not isinstance(value, WatchState):
        raise ValueError("state must be a WatchState")
    _integer(value.generation)
    _integer(value.retry_count)
    if value.status not in {"stopped", "connecting", "active", "waiting"}:
        raise ValueError("unknown watch state")
    if value.status == "stopped":
        if value.topic is not None or value.retry_count != 0:
            raise ValueError("stopped state invariant is invalid")
    elif not isinstance(value.topic, str) or not value.topic:
        raise ValueError("live state requires a topic")
    if value.status == "waiting" and value.retry_count < 1:
        raise ValueError("waiting state requires a retry")
    return value


def _disconnect_effect(state: WatchState) -> Effect:
    kind = "cancel_retry" if state.status == "waiting" else "disconnect"
    return Effect(kind, state.generation, state.topic)


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
        prior = () if current.status == "stopped" else (_disconnect_effect(current),)
        return WatchState("connecting", topic, generation, 0), (
            *prior,
            Effect("connect", generation, topic),
        )
    if kind == "connected":
        generation = _integer(event.get("generation"))
        if current.status != "connecting" or generation != current.generation:
            return current, ()
        return WatchState("active", current.topic, generation, 0), ()
    if kind == "connection_lost":
        generation = _integer(event.get("generation"))
        if current.status not in {"connecting", "active"} or generation != current.generation:
            return current, ()
        retry_count = current.retry_count + 1
        delay = min(2 ** (retry_count - 1), 8)
        return WatchState("waiting", current.topic, generation, retry_count), (
            Effect("schedule_retry", generation, current.topic, delay),
        )
    if kind == "retry_due":
        generation = _integer(event.get("generation"))
        if current.status != "waiting" or generation != current.generation:
            return current, ()
        next_generation = generation + 1
        return WatchState("connecting", current.topic, next_generation, current.retry_count), (
            Effect("connect", next_generation, current.topic),
        )
    if kind == "unsubscribe":
        if current.status == "stopped":
            return current, ()
        return WatchState("stopped", None, current.generation, 0), (_disconnect_effect(current),)
    raise ValueError("unknown event kind")
