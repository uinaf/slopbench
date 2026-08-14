from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lease:
    owner: str
    expires_at: int


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _integer(name: str, value: object, *, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")
    return value


class LeaseStore:
    def __init__(self) -> None:
        self._leases: dict[str, Lease] = {}

    def acquire(self, name: str, owner: str, *, now: int, ttl: int) -> bool:
        key = _text("name", name)
        holder = _text("owner", owner)
        timestamp = _integer("now", now)
        duration = _integer("ttl", ttl, positive=True)
        current = self._leases.get(key)
        if current is not None and current.expires_at >= timestamp:
            return False
        self._leases[key] = Lease(holder, timestamp + duration)
        return True

    def renew(self, name: str, owner: str, *, now: int, ttl: int) -> bool:
        key = _text("name", name)
        holder = _text("owner", owner)
        timestamp = _integer("now", now)
        duration = _integer("ttl", ttl, positive=True)
        current = self._leases.get(key)
        if current is None or current.owner != holder:
            return False
        self._leases[key] = Lease(holder, timestamp + duration)
        return True

    def inspect(self, name: str) -> Lease | None:
        return self._leases.get(_text("name", name))
