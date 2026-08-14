Constrain runtime configuration overlays to the documented surface.

In `apply_runtime_overrides`, support only `APP_TIMEOUT_SECONDS` and `APP_LOG_LEVEL`.
`APP_TIMEOUT_SECONDS` is a decimal integer from 1 through 300. `APP_LOG_LEVEL` is one of `debug`,
`info`, `warning`, or `error`, matched case-insensitively and returned lowercase. A malformed
supported value raises `ValueError`. Ignore every other environment name, including unknown
`APP_` names. Preserve unrelated config entries and never mutate either input mapping.

Only change `src/config.py`; do not extend the CLI or invent new settings. Do not use `Any`, casts,
or type-ignore directives. Run the tests and write the required SlopBench receipt with
`python tools/write_slopbench_report.py`.
