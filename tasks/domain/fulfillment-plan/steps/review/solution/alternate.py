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


def integer(value: object, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("invalid quantity")
    return value


def normalize(
    requested: object,
    warehouses: object,
    constraints: object,
) -> tuple[dict[str, int], list[tuple[str, dict[str, int]]]]:
    if not isinstance(requested, Mapping):
        raise ValueError("requested must be a mapping")
    order = {}
    for sku, quantity in requested.items():
        if not isinstance(sku, str) or not sku:
            raise ValueError("invalid requested SKU")
        order[sku] = integer(quantity, 1)
    if isinstance(warehouses, (str, bytes)) or not isinstance(warehouses, Sequence):
        raise ValueError("warehouses must be a sequence")
    available = []
    names = set()
    for warehouse in warehouses:
        if not isinstance(warehouse, Mapping):
            raise ValueError("invalid warehouse")
        name, stock = warehouse.get("name"), warehouse.get("stock")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("invalid warehouse name")
        if not isinstance(stock, Mapping):
            raise ValueError("invalid stock")
        normalized_stock = {}
        for sku, quantity in stock.items():
            if not isinstance(sku, str) or not sku:
                raise ValueError("invalid stock SKU")
            normalized_stock[sku] = integer(quantity, 0)
        names.add(name)
        available.append((name, normalized_stock))
    if constraints is None:
        constraints = {}
    if not isinstance(constraints, Mapping) or set(constraints) - names:
        raise ValueError("invalid constraints")
    constrained = []
    for name, stock in sorted(available):
        policy = constraints.get(name, {})
        if not isinstance(policy, Mapping) or set(policy) - {"blocked_skus", "reserved"}:
            raise ValueError("invalid warehouse constraint")
        blocked = policy.get("blocked_skus", [])
        if isinstance(blocked, (str, bytes)) or not isinstance(blocked, Sequence):
            raise ValueError("invalid blocked_skus")
        blocked = list(blocked)
        if any(not isinstance(sku, str) or not sku for sku in blocked) or len(blocked) != len(
            set(blocked)
        ):
            raise ValueError("invalid blocked_skus")
        reserved = policy.get("reserved", {})
        if not isinstance(reserved, Mapping):
            raise ValueError("invalid reserved inventory")
        effective = dict(stock)
        for sku, quantity in reserved.items():
            if not isinstance(sku, str) or sku not in stock:
                raise ValueError("invalid reserved SKU")
            quantity = integer(quantity, 0)
            if quantity > stock[sku]:
                raise ValueError("reservation exceeds stock")
            effective[sku] -= quantity
        for sku in blocked:
            effective[sku] = 0
        constrained.append((name, effective))
    return order, constrained


def plan_fulfillment(
    requested: Mapping[str, int],
    warehouses: Sequence[Mapping[str, object]],
    *,
    constraints: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[Shipment, ...]:
    order, available = normalize(requested, warehouses, constraints)
    if not order:
        return ()
    candidates = []
    for mask in range(1, 1 << len(available)):
        selected = [item for index, item in enumerate(available) if mask >> index & 1]
        if all(
            sum(stock.get(sku, 0) for _, stock in selected) >= quantity
            for sku, quantity in order.items()
        ):
            candidates.append(selected)
    if not candidates:
        raise InsufficientStock("warehouses cannot fulfill the request")
    selected = min(candidates, key=lambda group: (len(group), tuple(name for name, _ in group)))
    remaining = dict(order)
    shipments = []
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
