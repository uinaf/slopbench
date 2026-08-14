# Fixture instructions

- Change only `src/events.py`.
- Preserve the immutable `Event` value and `list_recent` behavior.
- Keep cursors opaque to callers and deterministic for the same input.
- Run `python -m unittest discover -s tests -v` after each change.
- Run `python tools/write_slopbench_report.py` after the final change.
- Do not add dependencies, access the network, or alter tests and tools.
