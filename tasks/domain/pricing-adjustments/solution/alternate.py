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
    total = _cents("subtotal_cents", subtotal_cents)
    try:
        iterator = iter(adjustments)
    except TypeError as exc:
        raise ValueError("adjustments must be a sequence") from exc
    for adjustment in iterator:
        if not isinstance(adjustment, Mapping):
            raise ValueError("adjustments must contain mappings")
        kind = adjustment.get("kind")
        if kind == "fixed":
            total -= _cents("amount_cents", adjustment.get("amount_cents"))
        elif kind == "percentage":
            basis_points = _cents("basis_points", adjustment.get("basis_points"))
            if basis_points > 10_000:
                raise ValueError("basis_points must not exceed 10,000")
            total -= (total * basis_points + 5_000) // 10_000
        else:
            raise ValueError("unknown adjustment kind")
        if total < 0:
            total = 0
    return total
