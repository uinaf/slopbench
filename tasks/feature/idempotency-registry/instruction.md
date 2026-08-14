Implement the repository idempotency boundary in `src/idempotency.py`.

`IdempotencyRegistry.execute(key, payload, operation)` accepts a non-empty string key, an immutable
`bytes` payload, and a zero-argument operation. On the first successful call, require the operation
to return a string and remember that result. A repeat with the same key and identical payload must
return the stored result without invoking the operation. The same key with different payload bytes
raises `IdempotencyConflict` without invoking the operation.

If the operation raises or returns a non-string, propagate the failure and record nothing so a
later retry may execute. Reject malformed keys, payloads, or non-callable operations with
`ValueError`. Only change `src/idempotency.py`. Run the tests and write the required SlopBench
receipt with `python tools/write_slopbench_report.py`.
