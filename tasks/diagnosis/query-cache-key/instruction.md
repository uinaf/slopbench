Repair the paginated response-cache incident reproduced by the public test suite.

Run `python -m unittest discover -s tests -v`, trace the failure to its root cause, and fix the
cache identity contract in `src/cache.py`. Cache keys must preserve raw paths and query strings,
including query order and duplicate parameters. URL fragments do not identify an HTTP response.
Scheme and authority matching remains case-insensitive, and an empty path is `/`.

Preserve the existing URL validation and public APIs. Change only `src/cache.py`; do not change
tests to hide the regression. After verification, write the required receipt with
`python tools/write_slopbench_report.py`.
