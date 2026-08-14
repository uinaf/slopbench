from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


class IdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class _Record:
    payload: bytes
    result: str


class IdempotencyRegistry:
    def __init__(self) -> None:
        self._records: dict[str, _Record] = {}

    def execute(self, key: str, payload: bytes, operation: Callable[[], str]) -> str:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        if not isinstance(payload, bytes):
            raise ValueError("payload must be bytes")
        if not callable(operation):
            raise ValueError("operation must be callable")
        previous = self._records.get(key)
        if previous is not None:
            if previous.payload != payload:
                raise IdempotencyConflict("key was already used with another payload")
            return previous.result
        result = operation()
        if not isinstance(result, str):
            raise ValueError("operation result must be a string")
        self._records[key] = _Record(payload, result)
        return result
