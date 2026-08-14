Review the repaired registry for the same expiry-boundary bug in lease renewal.

`renew` may succeed only when the named lease is still active at `now` and belongs to the caller.
An expired lease, including one at the exact expiry instant, must not be resurrected. Every failed
renewal leaves state unchanged; a successful renewal stores `now + ttl`. Preserve the phase-one
acquisition repair and every public API.

Only change `src/leases.py`. Run the repository tests and rewrite the SlopBench receipt with
`python tools/write_slopbench_report.py`.
