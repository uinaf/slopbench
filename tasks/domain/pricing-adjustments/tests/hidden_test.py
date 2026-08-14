from __future__ import annotations

import copy
import unittest

from src.pricing import apply_adjustments, quote_total


class HiddenPricingContract(unittest.TestCase):
    def test_adjustments_are_ordered_and_round_half_up(self) -> None:
        fixed_then_percentage = [
            {"kind": "fixed", "amount_cents": 100},
            {"kind": "percentage", "basis_points": 1_000},
        ]
        percentage_then_fixed = list(reversed(fixed_then_percentage))
        self.assertEqual(apply_adjustments(1_000, fixed_then_percentage), 810)
        self.assertEqual(apply_adjustments(1_000, percentage_then_fixed), 800)
        self.assertEqual(
            apply_adjustments(105, [{"kind": "percentage", "basis_points": 5_000}]),
            52,
        )

    def test_total_is_clamped_after_each_adjustment(self) -> None:
        self.assertEqual(
            apply_adjustments(
                100,
                [
                    {"kind": "fixed", "amount_cents": 150},
                    {"kind": "percentage", "basis_points": 5_000},
                ],
            ),
            0,
        )

    def test_inputs_are_not_mutated(self) -> None:
        adjustments = [
            {"kind": "fixed", "amount_cents": 25},
            {"kind": "percentage", "basis_points": 250},
        ]
        original = copy.deepcopy(adjustments)
        apply_adjustments(1_000, adjustments)
        self.assertEqual(adjustments, original)

    def test_invalid_boundary_values_are_rejected(self) -> None:
        invalid = [
            (True, []),
            (-1, []),
            (100, [{"kind": "fixed", "amount_cents": True}]),
            (100, [{"kind": "fixed", "amount_cents": -1}]),
            (100, [{"kind": "percentage", "basis_points": 10_001}]),
            (100, [{"kind": "percentage"}]),
            (100, [{"kind": "mystery", "amount_cents": 1}]),
        ]
        for subtotal, adjustments in invalid:
            with (
                self.subTest(subtotal=subtotal, adjustments=adjustments),
                self.assertRaises(ValueError),
            ):
                apply_adjustments(subtotal, adjustments)

    def test_legacy_contract_remains_available(self) -> None:
        self.assertEqual(quote_total(999, 1_000), 0)
        with self.assertRaises(ValueError):
            quote_total(True, 0)


if __name__ == "__main__":
    unittest.main()
