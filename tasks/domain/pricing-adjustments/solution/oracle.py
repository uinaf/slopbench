from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence


def _integer(name: str, value: object, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} exceeds {maximum}")
    return value


def quote_total(subtotal_cents: int, discount_cents: int) -> int:
    subtotal = _integer("subtotal_cents", subtotal_cents)
    discount = _integer("discount_cents", discount_cents)
    return max(0, subtotal - discount)


def _fixed(current: int, adjustment: Mapping[str, object]) -> int:
    return current - _integer("amount_cents", adjustment.get("amount_cents"))


def _percentage(current: int, adjustment: Mapping[str, object]) -> int:
    basis_points = _integer("basis_points", adjustment.get("basis_points"), maximum=10_000)
    reduction = (current * basis_points + 5_000) // 10_000
    return current - reduction


ADJUSTERS: dict[str, Callable[[int, Mapping[str, object]], int]] = {
    "fixed": _fixed,
    "percentage": _percentage,
}


def apply_adjustments(
    subtotal_cents: int,
    adjustments: Sequence[Mapping[str, object]],
) -> int:
    current = _integer("subtotal_cents", subtotal_cents)
    try:
        ordered = tuple(adjustments)
    except TypeError as exc:
        raise ValueError("adjustments must be a sequence") from exc
    for adjustment in ordered:
        if not isinstance(adjustment, Mapping):
            raise ValueError("each adjustment must be a mapping")
        try:
            adjust = ADJUSTERS[adjustment.get("kind")]
        except (KeyError, TypeError) as exc:
            raise ValueError("unknown adjustment kind") from exc
        current = max(0, adjust(current, adjustment))
    return current
