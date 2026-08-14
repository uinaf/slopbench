from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


class InsufficientStock(ValueError):
    pass


@dataclass(frozen=True)
class Shipment:
    warehouse: str
    items: tuple[tuple[str, int], ...]


def can_fulfill(requested: Mapping[str, int], stock: Mapping[str, int]) -> bool:
    return all(stock.get(sku, 0) >= quantity for sku, quantity in requested.items())


def plan_fulfillment(
    requested: Mapping[str, int],
    warehouses: Sequence[Mapping[str, object]],
    *,
    constraints: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[Shipment, ...]:
    remaining = dict(requested)
    shipments = []
    for warehouse in warehouses:
        items = []
        stock = warehouse["stock"]
        for sku in sorted(requested):
            quantity = min(remaining[sku], stock.get(sku, 0))
            if quantity:
                items.append((sku, quantity))
                remaining[sku] -= quantity
        if items:
            shipments.append(Shipment(warehouse["name"], tuple(items)))
        if not any(remaining.values()):
            return tuple(shipments)
    raise InsufficientStock("warehouses cannot fulfill the request")
