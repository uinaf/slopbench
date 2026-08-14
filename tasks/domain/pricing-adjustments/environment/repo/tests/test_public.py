from __future__ import annotations

import unittest

from src.pricing import apply_adjustments, quote_total


class PricingTests(unittest.TestCase):
    def test_legacy_quote_total_is_preserved(self) -> None:
        self.assertEqual(quote_total(1_000, 250), 750)
        self.assertEqual(quote_total(100, 500), 0)

    def test_fixed_adjustments_compose(self) -> None:
        adjustments = [
            {"kind": "fixed", "amount_cents": 125},
            {"kind": "fixed", "amount_cents": 75},
        ]
        self.assertEqual(apply_adjustments(1_000, adjustments), 800)

    def test_percentage_uses_basis_points(self) -> None:
        self.assertEqual(
            apply_adjustments(2_000, [{"kind": "percentage", "basis_points": 1_250}]),
            1_750,
        )


if __name__ == "__main__":
    unittest.main()
