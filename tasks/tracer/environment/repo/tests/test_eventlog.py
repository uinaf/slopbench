from __future__ import annotations

import copy
import unittest

from src.eventlog import summarize_events


class EventLogTests(unittest.TestCase):
    def test_groups_enabled_events_in_sorted_order(self) -> None:
        events = [
            {"kind": "write", "duration_ms": 8},
            {"kind": "read", "duration_ms": 3},
            {"kind": "write", "duration_ms": 5},
            {"kind": "read", "duration_ms": 100, "enabled": False},
        ]

        self.assertEqual(
            summarize_events(events),
            {
                "read": {"count": 1, "total_duration_ms": 3, "max_duration_ms": 3},
                "write": {"count": 2, "total_duration_ms": 13, "max_duration_ms": 8},
            },
        )

    def test_does_not_mutate_input(self) -> None:
        events = [{"kind": "read", "duration_ms": 3}]
        original = copy.deepcopy(events)

        summarize_events(events)

        self.assertEqual(events, original)


if __name__ == "__main__":
    unittest.main()
