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


def integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid generation")
    return value


def validate(state: object, event: object) -> tuple[WatchState, Mapping[str, object]]:
    if not isinstance(state, WatchState) or not isinstance(event, Mapping):
        raise ValueError("invalid transition input")
    integer(state.generation)
    if state.status not in ("stopped", "connecting", "active") or state.retry_count != 0:
        raise ValueError("invalid watch state")
    if (state.status == "stopped") != (state.topic is None):
        raise ValueError("state topic invariant is invalid")
    if state.topic is not None and (not isinstance(state.topic, str) or not state.topic):
        raise ValueError("invalid state topic")
    return state, event


def transition(
    state: WatchState,
    event: Mapping[str, object],
) -> tuple[WatchState, tuple[Effect, ...]]:
    state, event = validate(state, event)
    match event.get("kind"):
        case "subscribe":
            topic = event.get("topic")
            if not isinstance(topic, str) or topic == "":
                raise ValueError("subscribe requires a topic")
            if topic == state.topic:
                return state, ()
            generation = state.generation + 1
            effects = []
            if state.status != "stopped":
                effects.append(Effect("disconnect", state.generation, state.topic))
            effects.append(Effect("connect", generation, topic))
            return WatchState("connecting", topic, generation, 0), tuple(effects)
        case "connected":
            generation = integer(event.get("generation"))
            if state.status == "connecting" and generation == state.generation:
                return WatchState("active", state.topic, generation, 0), ()
            return state, ()
        case "unsubscribe" if state.status != "stopped":
            return WatchState("stopped", None, state.generation, 0), (
                Effect("disconnect", state.generation, state.topic),
            )
        case "unsubscribe":
            return state, ()
        case _:
            raise ValueError("unknown event kind")
