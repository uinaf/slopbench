from __future__ import annotations

from collections.abc import Callable


class IdempotencyConflict(ValueError):
    pass


class IdempotencyRegistry:
    def __init__(self) -> None:
        self._payloads: dict[str, bytes] = {}
        self._results: dict[str, str] = {}

    def execute(self, key: str, payload: bytes, operation: Callable[[], str]) -> str:
        if type(key) is not str or key == "":
            raise ValueError("invalid key")
        if type(payload) is not bytes:
            raise ValueError("invalid payload")
        if not callable(operation):
            raise ValueError("invalid operation")
        if key in self._payloads:
            if self._payloads[key] != payload:
                raise IdempotencyConflict("payload changed")
            return self._results[key]
        candidate = operation()
        if type(candidate) is not str:
            raise ValueError("invalid result")
        self._payloads[key] = bytes(payload)
        self._results[key] = candidate
        return candidate
