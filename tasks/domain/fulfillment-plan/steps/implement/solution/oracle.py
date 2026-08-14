from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations


class InsufficientStock(ValueError):
    pass


@dataclass(frozen=True)
class Shipment:
    warehouse: str
    items: tuple[tuple[str, int], ...]


def can_fulfill(requested: Mapping[str, int], stock: Mapping[str, int]) -> bool:
    return all(stock.get(sku, 0) >= quantity for sku, quantity in requested.items())


def _quantity(name: str, value: object, *, positive: bool) -> int:
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")
    return value


def _requested(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError("requested must be a mapping")
    requested: dict[str, int] = {}
    for sku, quantity in value.items():
        if not isinstance(sku, str) or not sku:
            raise ValueError("requested SKUs must be non-empty strings")
        requested[sku] = _quantity("requested quantity", quantity, positive=True)
    return requested


def _warehouses(value: object) -> list[tuple[str, dict[str, int]]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("warehouses must be a sequence")
    warehouses: list[tuple[str, dict[str, int]]] = []
    names: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("warehouse must be a mapping")
        name = raw.get("name")
        stock = raw.get("stock")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("warehouse names must be non-empty and unique")
        if not isinstance(stock, Mapping):
            raise ValueError("warehouse stock must be a mapping")
        normalized: dict[str, int] = {}
        for sku, quantity in stock.items():
            if not isinstance(sku, str) or not sku:
                raise ValueError("stock SKUs must be non-empty strings")
            normalized[sku] = _quantity("stock quantity", quantity, positive=False)
        names.add(name)
        warehouses.append((name, normalized))
    return sorted(warehouses)


def _covers(
    requested: Mapping[str, int],
    warehouses: Sequence[tuple[str, Mapping[str, int]]],
) -> bool:
    return all(
        sum(stock.get(sku, 0) for _, stock in warehouses) >= quantity
        for sku, quantity in requested.items()
    )


def plan_fulfillment(
    requested: Mapping[str, int],
    warehouses: Sequence[Mapping[str, object]],
) -> tuple[Shipment, ...]:
    order = _requested(requested)
    available = _warehouses(warehouses)
    if not order:
        return ()
    selected = next(
        (
            group
            for size in range(1, len(available) + 1)
            for group in combinations(available, size)
            if _covers(order, group)
        ),
        None,
    )
    if selected is None:
        raise InsufficientStock("warehouses cannot fulfill the request")
    remaining = dict(order)
    shipments: list[Shipment] = []
    for name, stock in selected:
        items = []
        for sku in sorted(order):
            quantity = min(remaining[sku], stock.get(sku, 0))
            if quantity:
                items.append((sku, quantity))
                remaining[sku] -= quantity
        if items:
            shipments.append(Shipment(name, tuple(items)))
    return tuple(shipments)
