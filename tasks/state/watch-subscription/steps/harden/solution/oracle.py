from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

ERROR_KINDS = frozenset(
    {"validation", "auth", "conflict", "rate_limit", "transient", "internal", "unknown"}
)
RETRYABLE_ERROR_KINDS = frozenset({"rate_limit", "transient"})
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


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("generation and retry_count must be non-negative integers")
    return value


def _error_kind(value: object) -> str:
    if not isinstance(value, str) or value not in ERROR_KINDS:
        raise ValueError("unknown error kind")
    return value


def _state(value: object) -> WatchState:
    if not isinstance(value, WatchState):
        raise ValueError("state must be a WatchState")
    _integer(value.generation)
    retry_count = _integer(value.retry_count)
    if retry_count > MAX_RETRIES:
        raise ValueError("retry count exceeds the retry bound")
    if value.status not in {"stopped", "connecting", "active", "waiting", "failed"}:
        raise ValueError("unknown watch state")
    if value.status == "stopped":
        if value.topic is not None or retry_count != 0 or value.error_kind is not None:
            raise ValueError("stopped state invariant is invalid")
        return value
    if not isinstance(value.topic, str) or not value.topic:
        raise ValueError("live state requires a topic")
    if value.status == "waiting" and retry_count < 1:
        raise ValueError("waiting state requires a retry")
    if value.status == "failed":
        _error_kind(value.error_kind)
    elif value.error_kind is not None:
        raise ValueError("only failed state carries an error kind")
    return value


def _topic(state: WatchState) -> str:
    if state.topic is None:
        raise ValueError("live state requires a topic")
    return state.topic


def _disconnect_effect(state: WatchState) -> Effect:
    kind = "cancel_retry" if state.status == "waiting" else "disconnect"
    return Effect(kind, state.generation, _topic(state))


def _terminal_failure(
    state: WatchState, generation: int, error_kind: str
) -> tuple[WatchState, tuple[Effect, ...]]:
    topic = _topic(state)
    failed = WatchState("failed", topic, generation, state.retry_count, error_kind)
    record = BoundaryEvent(
        event="watch_failed",
        operation="watch.connect",
        resource_id=topic,
        outcome="failed",
        error_kind=error_kind,
    )
    return failed, (Effect("emit_event", generation, topic, event=record),)


def _retry_or_fail(
    state: WatchState, generation: int, error_kind: str
) -> tuple[WatchState, tuple[Effect, ...]]:
    if error_kind not in RETRYABLE_ERROR_KINDS or state.retry_count >= MAX_RETRIES:
        return _terminal_failure(state, generation, error_kind)
    retry_count = state.retry_count + 1
    delay = min(2 ** (retry_count - 1), 8)
    topic = _topic(state)
    return WatchState("waiting", topic, generation, retry_count), (
        Effect("schedule_retry", generation, topic, delay),
    )


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
        if current.topic == topic and current.status != "failed":
            return current, ()
        generation = current.generation + 1
        prior = () if current.status in {"stopped", "failed"} else (_disconnect_effect(current),)
        return WatchState("connecting", topic, generation, 0), (
            *prior,
            Effect("connect", generation, topic),
        )
    if kind == "connected":
        generation = _integer(event.get("generation"))
        if current.status != "connecting" or generation != current.generation:
            return current, ()
        return WatchState("active", _topic(current), generation, 0), ()
    if kind == "connection_lost":
        generation = _integer(event.get("generation"))
        if current.status not in {"connecting", "active"} or generation != current.generation:
            return current, ()
        return _retry_or_fail(current, generation, "transient")
    if kind == "connection_failed":
        generation = _integer(event.get("generation"))
        error_kind = _error_kind(event.get("error_kind"))
        if current.status != "connecting" or generation != current.generation:
            return current, ()
        return _retry_or_fail(current, generation, error_kind)
    if kind == "retry_due":
        generation = _integer(event.get("generation"))
        if current.status != "waiting" or generation != current.generation:
            return current, ()
        next_generation = generation + 1
        return WatchState("connecting", _topic(current), next_generation, current.retry_count), (
            Effect("connect", next_generation, _topic(current)),
        )
    if kind == "unsubscribe":
        if current.status == "stopped":
            return current, ()
        stopped = WatchState("stopped", None, current.generation, 0)
        if current.status == "failed":
            return stopped, ()
        return stopped, (_disconnect_effect(current),)
    raise ValueError("unknown event kind")
