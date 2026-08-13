"""Event summarization boundary."""

from __future__ import annotations

from collections import defaultdict


def _validated(event: object) -> tuple[str, int, bool]:
    if not isinstance(event, dict):
        raise TypeError("each event must be a dictionary")
    if set(event) - {"kind", "duration_ms", "enabled"}:
        raise ValueError("event contains unknown fields")
    try:
        kind = event["kind"]
        duration = event["duration_ms"]
    except KeyError as exc:
        raise ValueError(f"missing event field: {exc.args[0]}") from exc
    enabled = event.get("enabled", True)
    if not isinstance(kind, str) or type(duration) is not int or type(enabled) is not bool:
        raise TypeError("event fields have invalid types")
    if kind == "" or duration < 0:
        raise ValueError("event fields have invalid values")
    return kind, duration, enabled


def summarize_events(events: object) -> dict[str, dict[str, int]]:
    if not isinstance(events, list):
        raise TypeError("events must be a list")
    grouped: dict[str, list[int]] = defaultdict(list)
    for raw_event in events:
        kind, duration, enabled = _validated(raw_event)
        if enabled:
            grouped[kind].append(duration)
    return {
        kind: {
            "count": len(grouped[kind]),
            "total_duration_ms": sum(grouped[kind]),
            "max_duration_ms": max(grouped[kind]),
        }
        for kind in sorted(grouped)
    }
