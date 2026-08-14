Repository verification is currently owned twice: immutable nodes in `src/task_graph.py` and the
parallel `scripts/verify.sh` sequence.

Retire the shell script and add a commandless `verify` node to `TASK_GRAPH`. It must depend on
`tests` and then `types`, in that order, so `resolve_tasks("verify")` returns those two leaf nodes
followed by the composite node. Preserve the existing leaf commands and graph validation.

Only change `src/task_graph.py` and remove `scripts/verify.sh`. Do not add another script or modify
the tests, event implementation, or tools. Run the repository tests and rewrite the SlopBench
receipt with `python tools/write_slopbench_report.py`.
