Harden reconnect failures at the process boundary.

Add a `connection_failed` event with a matching generation and a stable `error_kind` from
`validation`, `auth`, `conflict`, `rate_limit`, `transient`, `internal`, or `unknown`. Only
`rate_limit` and `transient` failures may retry. Schedule at most five retries; the next matching
failure enters a terminal `failed` state. Apply the same bound to `connection_lost`, treating it as
`transient`, while preserving the existing 1, 2, 4, 8, 8 delay sequence.

Represent each terminal failure as exactly one `emit_event` effect containing an immutable record
with the stable fields `event`, `operation`, `resource_id`, `outcome`, and `error_kind`. Derive the
classification only from `error_kind`; never include an input message, payload, or other event data
in the record. Duplicate terminal or stale failures are no-ops. A new subscription can recover from
failed, and unsubscribe resets failed without emitting a disconnect.

Preserve every prior transition and keep all effects as data without I/O. Only change
`src/watch.py`. Run the repository tests and rewrite the SlopBench report with
`python tools/write_slopbench_report.py`.
