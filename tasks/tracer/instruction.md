# Event summary tracer

Implement `summarize_events` in `/app/src/eventlog.py`.

The function accepts a list of event dictionaries. Every event must contain exactly:

- `kind`: a non-empty string
- `duration_ms`: a non-negative integer; booleans are not integers here
- optional `enabled`: a boolean that defaults to `true`

Validate every event, including disabled events. Raise `TypeError` for the wrong container or
field type and `ValueError` for an empty kind, a negative duration, or unknown fields. Do not
mutate the input.

Ignore disabled events in the result. Group enabled events by exact `kind`. Return a dictionary
whose keys are inserted in lexicographic order. Each value has exactly these integer fields:
`count`, `total_duration_ms`, and `max_duration_ms`. Return `{}` when no events are enabled.

Only change `src/eventlog.py`. Run `python tools/write_slopbench_report.py` when finished. That
command runs the public checks and writes the required `/app/slopbench-report.json` receipt. Read
`AGENTS.md` for the repository boundary.
