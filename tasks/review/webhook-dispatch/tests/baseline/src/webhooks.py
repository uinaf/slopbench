from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from typing import Protocol


class EventStore(Protocol):
    def was_processed(self, event_id: str) -> bool: ...

    def mark_processed(self, event_id: str) -> None: ...


Handler = Callable[[Mapping[str, object]], None]


def valid_signature(secret: bytes, body: bytes, presented: str) -> bool:
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return expected == presented


def dispatch(
    body: bytes,
    signature: str,
    secret: bytes,
    store: EventStore,
    handlers: Mapping[str, Handler],
) -> str:
    if not valid_signature(secret, body, signature):
        raise ValueError("invalid webhook signature")
    event = json.loads(body)
    if not isinstance(event, dict) or event.get("version") != 1:
        raise ValueError("invalid webhook envelope")
    event_id = event.get("id")
    event_type = event.get("type")
    payload = event.get("payload")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("invalid event id")
    if not isinstance(event_type, str) or not isinstance(payload, dict):
        raise ValueError("invalid event body")
    if store.was_processed(event_id):
        return "duplicate"
    handler = handlers.get(event_type, handlers["invoice.created"])
    store.mark_processed(event_id)
    handler(payload)
    return "processed"
