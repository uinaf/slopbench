from __future__ import annotations

import unittest

from src.leases import Lease, LeaseStore


class LeaseStoreTests(unittest.TestCase):
    def test_new_owner_can_acquire_at_exact_expiry(self) -> None:
        store = LeaseStore()
        self.assertTrue(store.acquire("invoice:7", "worker-a", now=10, ttl=5))
        self.assertTrue(store.acquire("invoice:7", "worker-b", now=15, ttl=4))
        self.assertEqual(store.inspect("invoice:7"), Lease("worker-b", 19))

    def test_active_owner_can_renew(self) -> None:
        store = LeaseStore()
        self.assertTrue(store.acquire("invoice:7", "worker-a", now=10, ttl=5))
        self.assertTrue(store.renew("invoice:7", "worker-a", now=12, ttl=8))
        self.assertEqual(store.inspect("invoice:7"), Lease("worker-a", 20))


if __name__ == "__main__":
    unittest.main()
