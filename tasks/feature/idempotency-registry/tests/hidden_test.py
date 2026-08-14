from __future__ import annotations

import unittest

from src.idempotency import IdempotencyConflict, IdempotencyRegistry


class HiddenIdempotencyContract(unittest.TestCase):
    def test_operation_failure_does_not_reserve_key(self) -> None:
        registry = IdempotencyRegistry()

        def fail() -> str:
            raise RuntimeError("temporary")

        with self.assertRaises(RuntimeError):
            registry.execute("request-1", b"payload", fail)
        self.assertEqual(registry.execute("request-1", b"payload", lambda: "ok"), "ok")

    def test_invalid_result_does_not_reserve_key(self) -> None:
        registry = IdempotencyRegistry()
        with self.assertRaises(ValueError):
            registry.execute("request-1", b"payload", lambda: 42)
        self.assertEqual(registry.execute("request-1", b"payload", lambda: "ok"), "ok")

    def test_conflict_never_invokes_operation(self) -> None:
        registry = IdempotencyRegistry()
        registry.execute("request-1", b"one", lambda: "ok")
        called = False

        def operation() -> str:
            nonlocal called
            called = True
            return "wrong"

        with self.assertRaises(IdempotencyConflict):
            registry.execute("request-1", b"two", operation)
        self.assertFalse(called)

    def test_malformed_boundary_values_are_rejected(self) -> None:
        registry = IdempotencyRegistry()
        for key, payload, operation in [
            ("", b"x", lambda: "ok"),
            ("key", bytearray(b"x"), lambda: "ok"),
            ("key", b"x", "not-callable"),
        ]:
            with self.subTest(key=key, payload=payload), self.assertRaises(ValueError):
                registry.execute(key, payload, operation)


if __name__ == "__main__":
    unittest.main()
