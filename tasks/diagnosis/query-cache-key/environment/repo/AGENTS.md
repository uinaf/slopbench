# Fixture instructions

- Change only `src/cache.py`.
- Preserve the public `Response` and `ResponseCache` APIs.
- Run `python -m unittest discover -s tests -v` to reproduce and verify the incident.
- Run `python tools/write_slopbench_report.py` after the final change.
- Do not add dependencies, access the network, or alter tests and tools.
