from __future__ import annotations

from collections.abc import Mapping, Sequence


def _cents(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def quote_total(subtotal_cents: int, discount_cents: int) -> int:
    return max(
        0, _cents("subtotal_cents", subtotal_cents) - _cents("discount_cents", discount_cents)
    )


def apply_adjustments(
    subtotal_cents: int,
    adjustments: Sequence[Mapping[str, object]],
) -> int:
    subtotal = _cents("subtotal_cents", subtotal_cents)
    fixed = 0
    percentage = 0
    for adjustment in adjustments:
        if adjustment.get("kind") == "fixed":
            fixed += _cents("amount_cents", adjustment.get("amount_cents"))
        elif adjustment.get("kind") == "percentage":
            basis_points = _cents("basis_points", adjustment.get("basis_points"))
            if basis_points > 10_000:
                raise ValueError("basis_points must not exceed 10,000")
            percentage += (subtotal * basis_points + 5_000) // 10_000
        else:
            raise ValueError("unknown adjustment kind")
    return max(0, subtotal - fixed - percentage)
