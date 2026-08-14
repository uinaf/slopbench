from __future__ import annotations

import unittest

from src.events import Event, list_recent, paginate_events


EVENTS = [
    Event("e1", 1, "created"),
    Event("e3", 3, "created"),
    Event("e2", 2, "updated"),
]


class EventPaginationTests(unittest.TestCase):
    def test_list_recent_remains_newest_first(self) -> None:
        self.assertEqual(
            [event.id for event in list_recent(EVENTS)], ["e3", "e2", "e1"]
        )

    def test_cursor_resumes_after_the_previous_page(self) -> None:
        first = paginate_events(EVENTS, limit=2)
        self.assertEqual([event.id for event in first.items], ["e3", "e2"])
        self.assertIsNotNone(first.next_cursor)
        second = paginate_events(EVENTS, limit=2, after=first.next_cursor)
        self.assertEqual([event.id for event in second.items], ["e1"])
        self.assertIsNone(second.next_cursor)


if __name__ == "__main__":
    unittest.main()
