from __future__ import annotations

import unittest

from src.sync import Effect, SyncState, transition


class HiddenSyncContract(unittest.TestCase):
    def test_stale_completions_are_ignored(self) -> None:
        current = SyncState("syncing", 4)
        for command in (
            {"kind": "succeeded", "generation": 3},
            {"kind": "failed", "generation": 5, "message": "late"},
        ):
            with self.subTest(command=command):
                self.assertEqual(transition(current, command), (current, ()))

    def test_start_is_idempotent_while_syncing(self) -> None:
        current = SyncState("syncing", 2)
        self.assertEqual(transition(current, {"kind": "start"}), (current, ()))

    def test_cancel_is_an_explicit_effect(self) -> None:
        current = SyncState("syncing", 7)
        self.assertEqual(
            transition(current, {"kind": "cancel"}),
            (SyncState("idle", 7), (Effect("cancel", 7),)),
        )
        failed = SyncState("failed", 7, "offline")
        self.assertEqual(transition(failed, {"kind": "cancel"}), (failed, ()))

    def test_invalid_states_and_commands_are_rejected(self) -> None:
        cases = [
            (SyncState("mystery", 0), {"kind": "start"}),
            (SyncState("idle", 0, "unexpected"), {"kind": "start"}),
            (SyncState("failed", 1), {"kind": "start"}),
            (SyncState("idle", True), {"kind": "start"}),
            (SyncState(), {"kind": "succeeded", "generation": True}),
            (SyncState("syncing", 1), {"kind": "failed", "generation": 1, "message": ""}),
            (SyncState(), {"kind": "unknown"}),
        ]
        for state, command in cases:
            with self.subTest(state=state, command=command), self.assertRaises(ValueError):
                transition(state, command)

    def test_inputs_remain_unchanged(self) -> None:
        state = SyncState("syncing", 2)
        command = {"kind": "failed", "generation": 2, "message": "offline"}
        transition(state, command)
        self.assertEqual(state, SyncState("syncing", 2))
        self.assertEqual(command, {"kind": "failed", "generation": 2, "message": "offline"})


if __name__ == "__main__":
    unittest.main()
