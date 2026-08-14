from __future__ import annotations


def _cents(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def quote_total(subtotal_cents: int, discount_cents: int) -> int:
    subtotal = _cents("subtotal_cents", subtotal_cents)
    discount = _cents("discount_cents", discount_cents)
    return max(0, subtotal - discount)
