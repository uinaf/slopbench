Add ordered pricing adjustments to the existing pricing module.

Implement `apply_adjustments(subtotal_cents, adjustments)` in `src/pricing.py`.
The subtotal is a non-negative integer number of cents. Each adjustment is a
mapping with a `kind` of `fixed` or `percentage`:

- `fixed` carries a non-negative integer `amount_cents`.
- `percentage` carries integer `basis_points` from 0 through 10,000.

Apply adjustments in the order supplied. A fixed adjustment subtracts its
amount. A percentage adjustment subtracts that percentage of the current
total, rounded to the nearest cent with half cents rounded up. Clamp the total
to zero after every adjustment.

Reject malformed inputs, booleans used as integers, unknown kinds, and values
outside these ranges with `ValueError`. Do not mutate the adjustment sequence
or its mappings. Preserve the behavior and public API of `quote_total`.

Only change `src/pricing.py`. Run the repository tests and write the required
SlopBench report with `python tools/write_slopbench_report.py`.
