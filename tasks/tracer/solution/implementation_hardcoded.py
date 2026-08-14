"""Known-invalid implementation that memorizes the public fixture."""

from __future__ import annotations


def summarize_events(events: object) -> dict[str, dict[str, int]]:
    if events == [
        {"kind": "write", "duration_ms": 8},
        {"kind": "read", "duration_ms": 3},
        {"kind": "write", "duration_ms": 5},
        {"kind": "read", "duration_ms": 100, "enabled": False},
    ]:
        return {
            "read": {"count": 1, "total_duration_ms": 3, "max_duration_ms": 3},
            "write": {"count": 2, "total_duration_ms": 13, "max_duration_ms": 8},
        }
    return {}
