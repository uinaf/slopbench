from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace


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
        raise ValueError("invalid non-negative integer")
    return value


def validate(state: object, event: object) -> tuple[WatchState, Mapping[str, object]]:
    if not isinstance(state, WatchState) or not isinstance(event, Mapping):
        raise ValueError("invalid transition input")
    integer(state.generation)
    integer(state.retry_count)
    if state.status not in ("stopped", "connecting", "active", "waiting"):
        raise ValueError("invalid watch status")
    if state.status == "stopped":
        if state.topic is not None or state.retry_count:
            raise ValueError("invalid stopped state")
    elif not isinstance(state.topic, str) or not state.topic:
        raise ValueError("live state requires a topic")
    if state.status == "waiting" and state.retry_count == 0:
        raise ValueError("waiting requires a retry")
    return state, event


def stop_effect(state: WatchState) -> Effect:
    return Effect(
        "cancel_retry" if state.status == "waiting" else "disconnect",
        state.generation,
        state.topic,
    )


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
            effects = [] if state.status == "stopped" else [stop_effect(state)]
            effects.append(Effect("connect", generation, topic))
            return WatchState("connecting", topic, generation, 0), tuple(effects)
        case "connected":
            generation = integer(event.get("generation"))
            if state.status == "connecting" and generation == state.generation:
                return WatchState("active", state.topic, generation, 0), ()
            return state, ()
        case "connection_lost":
            generation = integer(event.get("generation"))
            if state.status not in ("connecting", "active") or generation != state.generation:
                return state, ()
            retry = state.retry_count + 1
            waiting = replace(state, status="waiting", retry_count=retry)
            return waiting, (
                Effect("schedule_retry", generation, state.topic, min(2 ** (retry - 1), 8)),
            )
        case "retry_due":
            generation = integer(event.get("generation"))
            if state.status != "waiting" or generation != state.generation:
                return state, ()
            connecting = replace(state, status="connecting", generation=generation + 1)
            return connecting, (Effect("connect", connecting.generation, connecting.topic),)
        case "unsubscribe" if state.status != "stopped":
            return WatchState("stopped", None, state.generation, 0), (stop_effect(state),)
        case "unsubscribe":
            return state, ()
        case _:
            raise ValueError("unknown event kind")
