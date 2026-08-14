Fulfillment now receives warehouse availability constraints.

Extend `plan_fulfillment` with an optional keyword-only `constraints` mapping.
Each key names a declared warehouse. Its value may contain `blocked_skus`, a
sequence of SKU strings unavailable from that warehouse, and `reserved`, a
mapping of SKU to non-negative units that cannot be allocated. A reservation
may not exceed that warehouse's stock. Reject unknown warehouses, malformed
constraints, duplicates, and invalid quantities with `ValueError`.

Choose and allocate the minimum lexicographic warehouse set using effective
stock after blocked and reserved inventory are removed. Preserve every prior
call and result when constraints are omitted. Do not mutate any input.

Only change `src/fulfillment.py`. Run the repository tests and rewrite the
SlopBench report with `python tools/write_slopbench_report.py`.
