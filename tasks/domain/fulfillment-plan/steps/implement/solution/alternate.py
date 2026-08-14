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
    requested: object, warehouses: object
) -> tuple[dict[str, int], list[tuple[str, dict[str, int]]]]:
    if not isinstance(requested, Mapping):
        raise ValueError("requested must be a mapping")
    order = {}
    for sku, quantity in requested.items():
        if not isinstance(sku, str) or sku == "":
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
    return order, sorted(available)


def plan_fulfillment(
    requested: Mapping[str, int],
    warehouses: Sequence[Mapping[str, object]],
) -> tuple[Shipment, ...]:
    order, available = normalize(requested, warehouses)
    if not order:
        return ()
    candidates: list[tuple[int, tuple[str, ...], list[tuple[str, dict[str, int]]]]] = []
    for mask in range(1, 1 << len(available)):
        selected = [warehouse for index, warehouse in enumerate(available) if mask >> index & 1]
        if all(
            sum(stock.get(sku, 0) for _, stock in selected) >= quantity
            for sku, quantity in order.items()
        ):
            candidates.append((len(selected), tuple(name for name, _ in selected), selected))
    if not candidates:
        raise InsufficientStock("warehouses cannot fulfill the request")
    _, _, selected = min(candidates, key=lambda candidate: candidate[:2])
    remaining = dict(order)
    output = []
    for name, stock in selected:
        items = tuple((sku, min(remaining[sku], stock.get(sku, 0))) for sku in sorted(order))
        items = tuple((sku, quantity) for sku, quantity in items if quantity)
        for sku, quantity in items:
            remaining[sku] -= quantity
        if items:
            output.append(Shipment(name, items))
    return tuple(output)
