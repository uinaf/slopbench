from __future__ import annotations

import os
import unittest

from src.leases import Lease, LeaseStore


class ImplementContract(unittest.TestCase):
    def test_long_expired_lease_restarts_from_now(self) -> None:
        store = LeaseStore()
        self.assertTrue(store.acquire("job", "a", now=1, ttl=2))
        self.assertTrue(store.acquire("job", "b", now=20, ttl=5))
        self.assertEqual(store.inspect("job"), Lease("b", 25))

    def test_active_lease_is_not_stolen(self) -> None:
        store = LeaseStore()
        self.assertTrue(store.acquire("job", "a", now=1, ttl=10))
        self.assertFalse(store.acquire("job", "b", now=10, ttl=5))
        self.assertEqual(store.inspect("job"), Lease("a", 11))

    def test_boundaries_reject_bools_and_empty_names(self) -> None:
        store = LeaseStore()
        invalid = [
            ("", "a", 1, 1),
            ("job", "", 1, 1),
            ("job", "a", True, 1),
            ("job", "a", 1, 0),
        ]
        for name, owner, now, ttl in invalid:
            with (
                self.subTest(name=name, owner=owner, now=now, ttl=ttl),
                self.assertRaises(ValueError),
            ):
                store.acquire(name, owner, now=now, ttl=ttl)


class ReviewContract(ImplementContract):
    def test_expired_owner_cannot_renew_or_change_state(self) -> None:
        store = LeaseStore()
        store.acquire("job", "a", now=1, ttl=4)
        before = store.inspect("job")
        self.assertFalse(store.renew("job", "a", now=5, ttl=10))
        self.assertEqual(store.inspect("job"), before)

    def test_wrong_owner_cannot_renew_active_lease(self) -> None:
        store = LeaseStore()
        store.acquire("job", "a", now=1, ttl=10)
        before = store.inspect("job")
        self.assertFalse(store.renew("job", "b", now=2, ttl=10))
        self.assertEqual(store.inspect("job"), before)


def main() -> int:
    try:
        case = {"implement": ImplementContract, "review": ReviewContract}[
            os.environ.get("SLOPBENCH_PHASE")
        ]
    except KeyError as exc:
        raise SystemExit("unknown SLOPBENCH_PHASE") from exc
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(case)
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
