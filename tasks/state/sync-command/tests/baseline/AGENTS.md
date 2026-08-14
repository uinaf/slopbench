# Fixture instructions

- Change only `src/sync.py`.
- Keep state transitions pure; effects are returned as data.
- Run `python -m unittest discover -s tests -v`.
- Run `python tools/write_slopbench_report.py` after the final change.
- Do not add dependencies, access the network, or alter tests and tools.
