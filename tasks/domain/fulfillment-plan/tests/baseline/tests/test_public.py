from __future__ import annotations

import unittest

from src.fulfillment import InsufficientStock, Shipment, can_fulfill, plan_fulfillment


class FulfillmentTests(unittest.TestCase):
    def test_existing_stock_check_is_preserved(self) -> None:
        self.assertTrue(can_fulfill({"book": 2}, {"book": 3}))
        self.assertFalse(can_fulfill({"book": 2}, {"book": 1}))

    def test_one_warehouse_is_preferred(self) -> None:
        warehouses = [
            {"name": "central", "stock": {"book": 2}},
            {"name": "east", "stock": {"book": 1}},
        ]
        self.assertEqual(
            plan_fulfillment({"book": 2}, warehouses),
            (Shipment("central", (("book", 2),)),),
        )

    def test_insufficient_stock_is_explicit(self) -> None:
        with self.assertRaises(InsufficientStock):
            plan_fulfillment({"book": 3}, [{"name": "east", "stock": {"book": 2}}])


if __name__ == "__main__":
    unittest.main()
