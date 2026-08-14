from __future__ import annotations

import hashlib
import hmac
import json
import unittest

from src.webhooks import dispatch


class MemoryStore:
    def __init__(self, processed: set[str] | None = None) -> None:
        self.processed = set(processed or set())

    def was_processed(self, event_id: str) -> bool:
        return event_id in self.processed

    def mark_processed(self, event_id: str) -> None:
        self.processed.add(event_id)


def signed(body: bytes, secret: bytes = b"secret") -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


class WebhookTests(unittest.TestCase):
    def test_dispatches_a_valid_event_once(self) -> None:
        body = json.dumps(
            {
                "version": 1,
                "id": "evt-1",
                "type": "invoice.created",
                "payload": {"id": 7},
            }
        ).encode()
        observed = []
        store = MemoryStore()

        result = dispatch(
            body, signed(body), b"secret", store, {"invoice.created": observed.append}
        )

        self.assertEqual(result, "processed")
        self.assertEqual(observed, [{"id": 7}])
        self.assertEqual(store.processed, {"evt-1"})

    def test_rejects_an_invalid_signature(self) -> None:
        body = b'[{"not":"an event"}]'
        with self.assertRaises(ValueError):
            dispatch(
                body,
                "0" * 64,
                b"secret",
                MemoryStore(),
                {"invoice.created": lambda _: None},
            )

    def test_skips_an_already_processed_event(self) -> None:
        body = json.dumps(
            {"version": 1, "id": "evt-1", "type": "invoice.created", "payload": {}}
        ).encode()
        result = dispatch(
            body,
            signed(body),
            b"secret",
            MemoryStore({"evt-1"}),
            {"invoice.created": lambda _: self.fail("handler should not run")},
        )
        self.assertEqual(result, "duplicate")


if __name__ == "__main__":
    unittest.main()
