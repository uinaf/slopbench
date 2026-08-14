# Fixture instructions

- For event-feed behavior, change only `src/events.py`.
- For repository automation, extend `src/task_graph.py`; do not add another shell script.
- The legacy `scripts/verify.sh` may be removed only when its behavior is owned by the task graph.
- Preserve the immutable `Event` value and `list_recent` behavior.
- Keep cursors opaque to callers and deterministic for the same input.
- Run `python -m unittest discover -s tests -v` after each change.
- Run `python tools/write_slopbench_report.py` after the final change.
- Do not add dependencies, access the network, or alter tests and tools.
