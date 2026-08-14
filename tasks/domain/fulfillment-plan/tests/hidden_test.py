from __future__ import annotations

import copy
import os
import unittest

from src.fulfillment import InsufficientStock, Shipment, plan_fulfillment


class ImplementContract(unittest.TestCase):
    def test_minimum_shipments_beat_input_order(self) -> None:
        warehouses = [
            {"name": "a", "stock": {"book": 1}},
            {"name": "b", "stock": {"book": 1}},
            {"name": "z", "stock": {"book": 2}},
        ]
        self.assertEqual(
            plan_fulfillment({"book": 2}, warehouses),
            (Shipment("z", (("book", 2),)),),
        )

    def test_lexicographic_set_and_allocation_are_stable(self) -> None:
        warehouses = [
            {"name": "z", "stock": {"book": 2, "pen": 1}},
            {"name": "a", "stock": {"book": 2, "pen": 1}},
        ]
        self.assertEqual(
            plan_fulfillment({"pen": 1, "book": 2}, warehouses),
            (Shipment("a", (("book", 2), ("pen", 1))),),
        )

    def test_empty_and_invalid_inputs(self) -> None:
        self.assertEqual(plan_fulfillment({}, []), ())
        invalid = [
            ({"book": True}, []),
            ({"": 1}, []),
            ({"book": 0}, []),
            ({"book": 1}, [{"name": "a", "stock": {"book": -1}}]),
            (
                {"book": 1},
                [
                    {"name": "a", "stock": {"book": 1}},
                    {"name": "a", "stock": {"book": 1}},
                ],
            ),
        ]
        for requested, warehouses in invalid:
            with (
                self.subTest(requested=requested, warehouses=warehouses),
                self.assertRaises(ValueError),
            ):
                plan_fulfillment(requested, warehouses)

    def test_inputs_are_not_mutated(self) -> None:
        requested = {"book": 2}
        warehouses = [{"name": "a", "stock": {"book": 2}}]
        original = copy.deepcopy((requested, warehouses))
        plan_fulfillment(requested, warehouses)
        self.assertEqual((requested, warehouses), original)


class ReviewContract(unittest.TestCase):
    def test_blocked_and_reserved_stock_change_the_minimal_plan(self) -> None:
        warehouses = [
            {"name": "a", "stock": {"book": 2, "pen": 1}},
            {"name": "b", "stock": {"book": 2, "pen": 1}},
            {"name": "c", "stock": {"book": 1, "pen": 1}},
        ]
        constraints = {
            "a": {"blocked_skus": ["pen"]},
            "b": {"reserved": {"book": 1}},
        }
        self.assertEqual(
            plan_fulfillment({"book": 2, "pen": 1}, warehouses, constraints=constraints),
            (Shipment("a", (("book", 2),)), Shipment("b", (("pen", 1),))),
        )

    def test_old_calls_keep_their_result(self) -> None:
        warehouses = [
            {"name": "b", "stock": {"book": 2}},
            {"name": "a", "stock": {"book": 2}},
        ]
        self.assertEqual(
            plan_fulfillment({"book": 2}, warehouses),
            (Shipment("a", (("book", 2),)),),
        )

    def test_constraint_invariants_are_validated_without_mutation(self) -> None:
        warehouses = [{"name": "a", "stock": {"book": 2}}]
        invalid = [
            {"missing": {}},
            {"a": {"blocked_skus": ["book", "book"]}},
            {"a": {"reserved": {"book": 3}}},
            {"a": {"reserved": {"book": True}}},
        ]
        for constraints in invalid:
            with self.subTest(constraints=constraints), self.assertRaises(ValueError):
                plan_fulfillment({"book": 1}, warehouses, constraints=constraints)
        constraints = {"a": {"reserved": {"book": 1}}}
        original = copy.deepcopy(constraints)
        plan_fulfillment({"book": 1}, warehouses, constraints=constraints)
        self.assertEqual(constraints, original)

    def test_effective_shortage_is_explicit(self) -> None:
        with self.assertRaises(InsufficientStock):
            plan_fulfillment(
                {"book": 2},
                [{"name": "a", "stock": {"book": 2}}],
                constraints={"a": {"reserved": {"book": 1}}},
            )


def main() -> int:
    phase = os.environ.get("SLOPBENCH_PHASE")
    cases = {"implement": ImplementContract, "review": ReviewContract}
    try:
        case = cases[phase]
    except KeyError as exc:
        raise SystemExit(f"unknown SLOPBENCH_PHASE: {phase}") from exc
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(case)
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
