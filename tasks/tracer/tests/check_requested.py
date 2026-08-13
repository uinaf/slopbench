from __future__ import annotations

import copy
import sys
import unittest

sys.path.insert(0, "/app")

from src.eventlog import summarize_events


class RequestedContractTests(unittest.TestCase):
    def test_complete_summary_and_order(self) -> None:
        events = [
            {"kind": "zeta", "duration_ms": 0},
            {"kind": "alpha", "duration_ms": 4},
            {"kind": "alpha", "duration_ms": 7},
            {"kind": "zeta", "duration_ms": 999, "enabled": False},
        ]
        before = copy.deepcopy(events)

        result = summarize_events(events)

        self.assertEqual(list(result), ["alpha", "zeta"])
        self.assertEqual(
            result,
            {
                "alpha": {"count": 2, "total_duration_ms": 11, "max_duration_ms": 7},
                "zeta": {"count": 1, "total_duration_ms": 0, "max_duration_ms": 0},
            },
        )
        self.assertEqual(events, before)

    def test_empty_and_all_disabled(self) -> None:
        self.assertEqual(summarize_events([]), {})
        self.assertEqual(
            summarize_events([{"kind": "x", "duration_ms": 1, "enabled": False}]),
            {},
        )

    def test_rejects_wrong_containers_and_missing_fields(self) -> None:
        with self.assertRaises(TypeError):
            summarize_events(())
        with self.assertRaises(TypeError):
            summarize_events(["event"])
        with self.assertRaises(ValueError):
            summarize_events([{"kind": "x"}])

    def test_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValueError):
            summarize_events([{"kind": "x", "duration_ms": 1, "extra": 2}])

    def test_rejects_invalid_kinds(self) -> None:
        with self.assertRaises(TypeError):
            summarize_events([{"kind": 1, "duration_ms": 1}])
        with self.assertRaises(ValueError):
            summarize_events([{"kind": "", "duration_ms": 1}])

    def test_rejects_invalid_durations(self) -> None:
        with self.assertRaises(TypeError):
            summarize_events([{"kind": "x", "duration_ms": True}])
        with self.assertRaises(ValueError):
            summarize_events([{"kind": "x", "duration_ms": -1}])

    def test_validates_disabled_events(self) -> None:
        with self.assertRaises(TypeError):
            summarize_events([{"kind": "x", "duration_ms": "bad", "enabled": False}])
        with self.assertRaises(TypeError):
            summarize_events([{"kind": "x", "duration_ms": 1, "enabled": 1}])


if __name__ == "__main__":
    unittest.main()
