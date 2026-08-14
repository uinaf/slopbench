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


def _quantity(value: object, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("invalid quantity")
    return value


def _normalize_requested(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError("requested must be a mapping")
    result = {}
    for sku, quantity in value.items():
        if not isinstance(sku, str) or not sku:
            raise ValueError("requested SKUs must be non-empty strings")
        result[sku] = _quantity(quantity, 1)
    return result


def _normalize_warehouses(value: object) -> list[tuple[str, dict[str, int]]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("warehouses must be a sequence")
    output = []
    names = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("warehouse must be a mapping")
        name, stock = raw.get("name"), raw.get("stock")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("warehouse names must be non-empty and unique")
        if not isinstance(stock, Mapping):
            raise ValueError("warehouse stock must be a mapping")
        normalized = {}
        for sku, quantity in stock.items():
            if not isinstance(sku, str) or not sku:
                raise ValueError("stock SKUs must be non-empty strings")
            normalized[sku] = _quantity(quantity, 0)
        names.add(name)
        output.append((name, normalized))
    return sorted(output)


def _effective_stock(
    warehouses: Sequence[tuple[str, dict[str, int]]],
    constraints: object,
) -> list[tuple[str, dict[str, int]]]:
    if constraints is None:
        return [(name, dict(stock)) for name, stock in warehouses]
    if not isinstance(constraints, Mapping):
        raise ValueError("constraints must be a mapping")
    by_name = dict(warehouses)
    if set(constraints) - set(by_name):
        raise ValueError("constraint names must identify declared warehouses")
    output = []
    for name, stock in warehouses:
        raw = constraints.get(name, {})
        if not isinstance(raw, Mapping) or set(raw) - {"blocked_skus", "reserved"}:
            raise ValueError("warehouse constraint is malformed")
        blocked_raw = raw.get("blocked_skus", [])
        if isinstance(blocked_raw, (str, bytes)) or not isinstance(blocked_raw, Sequence):
            raise ValueError("blocked_skus must be a sequence")
        blocked = list(blocked_raw)
        if any(not isinstance(sku, str) or not sku for sku in blocked) or len(blocked) != len(
            set(blocked)
        ):
            raise ValueError("blocked_skus must be unique non-empty strings")
        reserved_raw = raw.get("reserved", {})
        if not isinstance(reserved_raw, Mapping):
            raise ValueError("reserved must be a mapping")
        reserved = {}
        for sku, quantity in reserved_raw.items():
            if not isinstance(sku, str) or not sku or sku not in stock:
                raise ValueError("reserved SKU is invalid")
            reserved[sku] = _quantity(quantity, 0)
            if reserved[sku] > stock[sku]:
                raise ValueError("reserved quantity exceeds stock")
        effective = {
            sku: 0 if sku in blocked else quantity - reserved.get(sku, 0)
            for sku, quantity in stock.items()
        }
        output.append((name, effective))
    return output


def plan_fulfillment(
    requested: Mapping[str, int],
    warehouses: Sequence[Mapping[str, object]],
    *,
    constraints: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[Shipment, ...]:
    order = _normalize_requested(requested)
    available = _effective_stock(_normalize_warehouses(warehouses), constraints)
    if not order:
        return ()
    selected = next(
        (
            group
            for size in range(1, len(available) + 1)
            for group in combinations(available, size)
            if all(
                sum(stock.get(sku, 0) for _, stock in group) >= quantity
                for sku, quantity in order.items()
            )
        ),
        None,
    )
    if selected is None:
        raise InsufficientStock("warehouses cannot fulfill the request")
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
