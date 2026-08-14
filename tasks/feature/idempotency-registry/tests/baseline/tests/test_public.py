from __future__ import annotations

import unittest

from src.idempotency import IdempotencyConflict, IdempotencyRegistry


class IdempotencyRegistryTests(unittest.TestCase):
    def test_success_is_replayed_without_running_operation_again(self) -> None:
        registry = IdempotencyRegistry()
        calls = 0

        def operation() -> str:
            nonlocal calls
            calls += 1
            return "created-17"

        self.assertEqual(
            registry.execute("request-1", b"amount=5", operation), "created-17"
        )
        self.assertEqual(
            registry.execute("request-1", b"amount=5", operation), "created-17"
        )
        self.assertEqual(calls, 1)

    def test_same_key_with_different_payload_conflicts(self) -> None:
        registry = IdempotencyRegistry()
        registry.execute("request-1", b"amount=5", lambda: "created-17")
        with self.assertRaises(IdempotencyConflict):
            registry.execute("request-1", b"amount=6", lambda: "created-18")


if __name__ == "__main__":
    unittest.main()
