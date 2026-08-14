from __future__ import annotations

import unittest

from src.watch import Effect, WatchState, is_active, transition


class WatchTests(unittest.TestCase):
    def test_existing_active_helper_is_preserved(self) -> None:
        self.assertTrue(is_active("active"))
        self.assertFalse(is_active("connecting"))

    def test_subscribe_connect_and_unsubscribe(self) -> None:
        connecting, effects = transition(
            WatchState(), {"kind": "subscribe", "topic": "orders"}
        )
        self.assertEqual(connecting, WatchState("connecting", "orders", 1, 0))
        self.assertEqual(effects, (Effect("connect", 1, "orders"),))
        active, effects = transition(connecting, {"kind": "connected", "generation": 1})
        self.assertEqual(active, WatchState("active", "orders", 1, 0))
        self.assertEqual(effects, ())
        stopped, effects = transition(active, {"kind": "unsubscribe"})
        self.assertEqual(stopped, WatchState("stopped", None, 1, 0))
        self.assertEqual(effects, (Effect("disconnect", 1, "orders"),))


if __name__ == "__main__":
    unittest.main()
