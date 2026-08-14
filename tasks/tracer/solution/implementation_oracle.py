"""Event summarization boundary."""

from __future__ import annotations


def summarize_events(events: object) -> dict[str, dict[str, int]]:
    if not isinstance(events, list):
        raise TypeError("events must be a list")
    summaries: dict[str, dict[str, int]] = {}
    for event in events:
        if not isinstance(event, dict):
            raise TypeError("each event must be a dictionary")
        unknown = set(event) - {"kind", "duration_ms", "enabled"}
        if unknown:
            raise ValueError(f"unknown event fields: {sorted(unknown)}")
        if "kind" not in event or "duration_ms" not in event:
            raise ValueError("kind and duration_ms are required")
        kind = event["kind"]
        duration = event["duration_ms"]
        enabled = event.get("enabled", True)
        if not isinstance(kind, str):
            raise TypeError("kind must be a string")
        if not kind:
            raise ValueError("kind must not be empty")
        if type(duration) is not int:
            raise TypeError("duration_ms must be an integer")
        if duration < 0:
            raise ValueError("duration_ms must not be negative")
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        if not enabled:
            continue
        summary = summaries.setdefault(
            kind,
            {"count": 0, "total_duration_ms": 0, "max_duration_ms": 0},
        )
        summary["count"] += 1
        summary["total_duration_ms"] += duration
        summary["max_duration_ms"] = max(summary["max_duration_ms"], duration)
    return {kind: summaries[kind] for kind in sorted(summaries)}
