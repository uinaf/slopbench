from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lease:
    owner: str
    expires_at: int


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"invalid {name}")
    return value


def _number(name: str, value: object, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"invalid {name}")
    return value


class LeaseStore:
    def __init__(self) -> None:
        self._leases: dict[str, Lease] = {}

    @staticmethod
    def _active(lease: Lease | None, now: int) -> bool:
        return lease is not None and now < lease.expires_at

    def acquire(self, name: str, owner: str, *, now: int, ttl: int) -> bool:
        key, holder = _text("name", name), _text("owner", owner)
        timestamp, duration = _number("now", now, 0), _number("ttl", ttl, 1)
        if self._active(self._leases.get(key), timestamp):
            return False
        self._leases[key] = Lease(holder, timestamp + duration)
        return True

    def renew(self, name: str, owner: str, *, now: int, ttl: int) -> bool:
        key, holder = _text("name", name), _text("owner", owner)
        timestamp, duration = _number("now", now, 0), _number("ttl", ttl, 1)
        existing = self._leases.get(key)
        if not self._active(existing, timestamp) or existing.owner != holder:
            return False
        self._leases[key] = Lease(holder, timestamp + duration)
        return True

    def inspect(self, name: str) -> Lease | None:
        return self._leases.get(_text("name", name))
