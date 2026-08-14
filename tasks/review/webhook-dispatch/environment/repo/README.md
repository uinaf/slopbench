# Webhook dispatcher

`dispatch` verifies the raw request body, parses a versioned event, and calls the handler registered
for that exact event type. Unknown event types must be rejected without side effects. A successful
handler call is marked processed so later delivery of the same event ID returns `duplicate`.

Failed handlers remain retryable and must not mark the event processed. Delivery is serialized per
event ID by the caller, so cross-process claiming is outside this fixture. HMAC-SHA256 is the
required algorithm; the presented lowercase hexadecimal signature remains attacker-controlled.
