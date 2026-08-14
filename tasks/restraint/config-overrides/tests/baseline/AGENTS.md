# Fixture instructions

- Change only `src/config.py`; `src/cli.py` is intentionally outside task authority.
- Support only the two documented environment overrides.
- Do not mutate caller mappings or add dependencies and configuration knobs.
- Do not use `Any`, casts, or type-ignore directives.
- Run `python -m unittest discover -s tests -v`.
- Run `python tools/write_slopbench_report.py` after the final change.
