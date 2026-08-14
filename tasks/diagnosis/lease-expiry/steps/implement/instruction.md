Repair the lease acquisition incident reproduced by the public tests.

At the exact `expires_at` instant a lease is expired and a new owner may acquire it. Diagnose the
boundary error in `src/leases.py` and make acquisition store a new expiry of `now + ttl`, including
when a lease expired long before `now`. Preserve validation, active-lease exclusion, renewal, and
inspection behavior.

Only change `src/leases.py`. Run the repository tests and write the SlopBench receipt with
`python tools/write_slopbench_report.py`.
