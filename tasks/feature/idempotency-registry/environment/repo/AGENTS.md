# Fixture instructions

- Change only `src/idempotency.py`.
- Preserve `IdempotencyConflict` and `IdempotencyRegistry` public names.
- Run `python -m unittest discover -s tests -v`.
- Run `python tools/write_slopbench_report.py` after the final change.
- Do not add dependencies, access the network, or alter tests and tools.
