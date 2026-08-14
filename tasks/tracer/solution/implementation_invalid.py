"""Known-invalid tracer implementation."""

from __future__ import annotations


def summarize_events(events: object) -> dict[str, dict[str, int]]:
    summaries: dict[str, dict[str, int]] = {}
    if not isinstance(events, list):
        return summaries
    for event in events:
        kind = event["kind"]
        duration = event["duration_ms"]
        summary = summaries.setdefault(
            kind,
            {"count": 0, "total_duration_ms": 0, "max_duration_ms": 0},
        )
        summary["count"] += 1
        summary["total_duration_ms"] += duration
        summary["max_duration_ms"] = max(summary["max_duration_ms"], duration)
    return summaries
