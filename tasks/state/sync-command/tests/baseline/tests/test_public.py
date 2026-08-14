from __future__ import annotations

import unittest

from src.sync import Effect, SyncState, transition


class SyncTests(unittest.TestCase):
    def test_start_emits_upload_and_success_returns_idle(self) -> None:
        syncing, effects = transition(SyncState(), {"kind": "start"})
        self.assertEqual(syncing, SyncState("syncing", 1))
        self.assertEqual(effects, (Effect("upload", 1),))

        idle, effects = transition(syncing, {"kind": "succeeded", "generation": 1})
        self.assertEqual(idle, SyncState("idle", 1))
        self.assertEqual(effects, ())

    def test_failure_can_be_retried(self) -> None:
        failed, _ = transition(
            SyncState("syncing", 3),
            {"kind": "failed", "generation": 3, "message": "offline"},
        )
        retried, effects = transition(failed, {"kind": "start"})
        self.assertEqual(retried, SyncState("syncing", 4))
        self.assertEqual(effects, (Effect("upload", 4),))


if __name__ == "__main__":
    unittest.main()
