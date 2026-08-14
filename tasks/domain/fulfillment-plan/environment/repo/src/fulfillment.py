from __future__ import annotations

from collections.abc import Mapping


class InsufficientStock(ValueError):
    pass


def can_fulfill(requested: Mapping[str, int], stock: Mapping[str, int]) -> bool:
    return all(stock.get(sku, 0) >= quantity for sku, quantity in requested.items())
