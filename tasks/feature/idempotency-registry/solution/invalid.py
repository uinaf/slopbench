from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


class IdempotencyConflict(ValueError):
    pass


@dataclass
class _Record:
    payload: bytes
    result: str = ""


class IdempotencyRegistry:
    def __init__(self) -> None:
        self._records: dict[str, _Record] = {}

    def execute(self, key: str, payload: bytes, operation: Callable[[], str]) -> str:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        if not isinstance(payload, bytes) or not callable(operation):
            raise ValueError("invalid request")
        previous = self._records.get(key)
        if previous is not None:
            if previous.payload != payload:
                raise IdempotencyConflict("payload changed")
            return previous.result
        record = _Record(payload)
        self._records[key] = record
        record.result = operation()
        if not isinstance(record.result, str):
            raise ValueError("operation result must be a string")
        return record.result
