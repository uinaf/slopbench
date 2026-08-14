Review this repository for concrete defects without modifying tracked files.

Read `AGENTS.md`, `README.md`, and `REVIEW_GUIDE.md`. Inspect the implementation and tests, then
write `slopbench-review.json` using the documented `slopbench.review.v1` structure. Report only
behavioral defects that are supported by the repository contract. Keep every location to one line
or a tight range of at most five lines. Do not propose patches or report style preferences.

After writing the review, run `python tools/write_slopbench_report.py` to bind the submission to
the task and repository revision.
