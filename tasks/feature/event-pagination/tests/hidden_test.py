from __future__ import annotations

import os
import unittest
from pathlib import Path

from src.events import Event, paginate_events


class ImplementContract(unittest.TestCase):
    def test_equal_timestamps_are_paginated_by_id_without_skips(self) -> None:
        events = [Event("a", 5, "x"), Event("c", 5, "x"), Event("b", 5, "x")]
        first = paginate_events(events, limit=1)
        second = paginate_events(events, limit=1, after=first.next_cursor)
        third = paginate_events(events, limit=1, after=second.next_cursor)
        self.assertEqual(
            [first.items[0].id, second.items[0].id, third.items[0].id], ["c", "b", "a"]
        )
        self.assertIsNone(third.next_cursor)

    def test_invalid_boundaries_and_unknown_cursor_fail(self) -> None:
        events = [Event("a", 1, "x")]
        for limit in (0, 101, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                paginate_events(events, limit=limit)
        for cursor in ("", "not-a-cursor"):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                paginate_events(events, limit=1, after=cursor)
        with self.assertRaises(ValueError):
            paginate_events([Event("a", 1, "x"), Event("a", 2, "x")], limit=1)


class ReviewContract(ImplementContract):
    def test_filter_is_applied_before_limit(self) -> None:
        events = [
            Event("d", 4, "skip"),
            Event("c", 3, "wanted"),
            Event("b", 2, "skip"),
            Event("a", 1, "wanted"),
        ]
        page = paginate_events(events, limit=2, kind="wanted")
        self.assertEqual([event.id for event in page.items], ["c", "a"])
        self.assertIsNone(page.next_cursor)

    def test_cursor_is_bound_to_filter(self) -> None:
        events = [Event("c", 3, "x"), Event("b", 2, "x"), Event("a", 1, "y")]
        page = paginate_events(events, limit=1, kind="x")
        with self.assertRaises(ValueError):
            paginate_events(events, limit=1, after=page.next_cursor, kind="y")
        with self.assertRaises(ValueError):
            paginate_events(events, limit=1, after=page.next_cursor)


class IntegrateContract(ReviewContract):
    def test_verification_is_owned_by_the_typed_task_graph(self) -> None:
        from src.task_graph import TASK_GRAPH, TaskNode, resolve_tasks

        nodes = resolve_tasks("verify")
        self.assertEqual(
            nodes,
            (
                TaskNode(
                    "tests",
                    ("python", "-m", "unittest", "discover", "-s", "tests", "-v"),
                ),
                TaskNode(
                    "types",
                    ("python", "-m", "compileall", "-q", "src", "tests", "tools"),
                ),
                TaskNode("verify", (), ("tests", "types")),
            ),
        )
        self.assertEqual(TASK_GRAPH, nodes)

    def test_parallel_shell_owner_is_removed(self) -> None:
        self.assertEqual(list(Path("scripts").glob("*.sh")), [])


def main() -> int:
    try:
        case = {
            "implement": ImplementContract,
            "review": ReviewContract,
            "integrate": IntegrateContract,
        }[os.environ.get("SLOPBENCH_PHASE")]
    except KeyError as exc:
        raise SystemExit("unknown SLOPBENCH_PHASE") from exc
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(case)
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
