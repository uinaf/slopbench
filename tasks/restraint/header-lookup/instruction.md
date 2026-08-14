Fix case-insensitive lookup in `get_header` without broadening the change.

Header names compare case-insensitively. Return the original value from the first matching pair;
never normalize values or collapse or reorder duplicates. Preserve input validation and leave
`canonicalize_headers` behavior unchanged.

Only change `src/headers.py`. Do not edit the compatibility module, tests, or tools, and do not use
`Any`, casts, or type-ignore directives. Run the tests and write the required SlopBench receipt
with `python tools/write_slopbench_report.py`.
