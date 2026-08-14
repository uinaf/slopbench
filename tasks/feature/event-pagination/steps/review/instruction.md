Extend event pagination with an optional keyword-only `kind` filter.

`kind` is either `None` or a non-empty string. Filter before applying the limit or cursor so every
page can contain up to `limit` matching events. Cursors must be bound to the filter that created
them; using a filtered cursor with another kind or no filter raises `ValueError`. Preserve all
phase-one results when `kind` is omitted, including ordering and validation.

Only change `src/events.py`, run the tests, and rewrite the SlopBench receipt with
`python tools/write_slopbench_report.py`.
