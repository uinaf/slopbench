from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

ERROR_KINDS = (
    "validation",
    "auth",
    "conflict",
    "rate_limit",
    "transient",
    "internal",
    "unknown",
)
MAX_RETRIES = 5


def is_active(status: str) -> bool:
    return status == "active"


@dataclass(frozen=True)
class WatchState:
    status: str = "stopped"
    topic: str | None = None
    generation: int = 0
    retry_count: int = 0
    error_kind: str | None = None


@dataclass(frozen=True)
class BoundaryEvent:
    event: str
    operation: str
    resource_id: str
    outcome: str
    error_kind: str


@dataclass(frozen=True)
class Effect:
    kind: str
    generation: int
    topic: str
    delay_seconds: int | None = None
    event: BoundaryEvent | None = None


def integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid non-negative integer")
    return value


def failure_kind(value: object) -> str:
    if not isinstance(value, str) or value not in ERROR_KINDS:
        raise ValueError("invalid error kind")
    return value


def validate(state: object, event: object) -> tuple[WatchState, Mapping[str, object]]:
    if not isinstance(state, WatchState) or not isinstance(event, Mapping):
        raise ValueError("invalid transition input")
    integer(state.generation)
    retry_count = integer(state.retry_count)
    if retry_count > MAX_RETRIES:
        raise ValueError("retry bound exceeded")
    if state.status not in ("stopped", "connecting", "active", "waiting", "failed"):
        raise ValueError("invalid watch status")
    if state.status == "stopped":
        if state.topic is not None or retry_count or state.error_kind is not None:
            raise ValueError("invalid stopped state")
    elif not isinstance(state.topic, str) or not state.topic:
        raise ValueError("live state requires a topic")
    if state.status == "waiting" and retry_count == 0:
        raise ValueError("waiting requires a retry")
    if state.status == "failed":
        failure_kind(state.error_kind)
    elif state.error_kind is not None:
        raise ValueError("unexpected error kind")
    return state, event


def topic(state: WatchState) -> str:
    if state.topic is None:
        raise ValueError("missing topic")
    return state.topic


def stop_effect(state: WatchState) -> Effect:
    return Effect(
        "cancel_retry" if state.status == "waiting" else "disconnect",
        state.generation,
        topic(state),
    )


def failed(
    state: WatchState, generation: int, error_kind: str
) -> tuple[WatchState, tuple[Effect, ...]]:
    resource_id = topic(state)
    record = BoundaryEvent("watch_failed", "watch.connect", resource_id, "failed", error_kind)
    return replace(state, status="failed", error_kind=error_kind), (
        Effect("emit_event", generation, resource_id, event=record),
    )


def retry(
    state: WatchState, generation: int, error_kind: str
) -> tuple[WatchState, tuple[Effect, ...]]:
    if error_kind not in ("rate_limit", "transient") or state.retry_count == MAX_RETRIES:
        return failed(state, generation, error_kind)
    retry_count = state.retry_count + 1
    waiting = replace(state, status="waiting", retry_count=retry_count)
    return waiting, (
        Effect(
            "schedule_retry",
            generation,
            topic(state),
            min(2 ** (retry_count - 1), 8),
        ),
    )


def transition(
    state: WatchState,
    event: Mapping[str, object],
) -> tuple[WatchState, tuple[Effect, ...]]:
    state, event = validate(state, event)
    match event.get("kind"):
        case "subscribe":
            next_topic = event.get("topic")
            if not isinstance(next_topic, str) or next_topic == "":
                raise ValueError("subscribe requires a topic")
            if next_topic == state.topic and state.status != "failed":
                return state, ()
            generation = state.generation + 1
            effects = [] if state.status in ("stopped", "failed") else [stop_effect(state)]
            effects.append(Effect("connect", generation, next_topic))
            return WatchState("connecting", next_topic, generation, 0), tuple(effects)
        case "connected":
            generation = integer(event.get("generation"))
            if state.status == "connecting" and generation == state.generation:
                return WatchState("active", topic(state), generation, 0), ()
            return state, ()
        case "connection_lost":
            generation = integer(event.get("generation"))
            if state.status in ("connecting", "active") and generation == state.generation:
                return retry(state, generation, "transient")
            return state, ()
        case "connection_failed":
            generation = integer(event.get("generation"))
            error_kind = failure_kind(event.get("error_kind"))
            if state.status == "connecting" and generation == state.generation:
                return retry(state, generation, error_kind)
            return state, ()
        case "retry_due":
            generation = integer(event.get("generation"))
            if state.status != "waiting" or generation != state.generation:
                return state, ()
            connecting = replace(state, status="connecting", generation=generation + 1)
            return connecting, (Effect("connect", connecting.generation, topic(connecting)),)
        case "unsubscribe" if state.status == "failed":
            return WatchState("stopped", None, state.generation, 0), ()
        case "unsubscribe" if state.status != "stopped":
            return WatchState("stopped", None, state.generation, 0), (stop_effect(state),)
        case "unsubscribe":
            return state, ()
        case _:
            raise ValueError("unknown event kind")
