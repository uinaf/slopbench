from __future__ import annotations

from collections.abc import Callable


class IdempotencyConflict(ValueError):
    pass


class IdempotencyRegistry:
    def execute(self, key: str, payload: bytes, operation: Callable[[], str]) -> str:
        return operation()
