# Fixture instructions

- Change only `src/leases.py`.
- Preserve the `Lease` and `LeaseStore` public APIs.
- Treat supplied integer timestamps as the only clock; do not read wall time.
- Run `python -m unittest discover -s tests -v` after each repair.
- Run `python tools/write_slopbench_report.py` after the final change.
- Do not add dependencies, access the network, or alter tests and tools.
