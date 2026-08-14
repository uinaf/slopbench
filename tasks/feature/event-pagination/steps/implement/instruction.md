Add opaque cursor pagination to `src/events.py`.

Define an immutable `EventPage` with `items: tuple[Event, ...]` and `next_cursor: str | None`, then
implement `paginate_events(events, *, limit, after=None)`. Validate every event, require unique
IDs, and order newest first by `(created_at, id)`. `limit` is an integer from 1 through 100.
Return items after the cursor and emit a cursor only when more items remain. Reject malformed or
unknown cursors with `ValueError`. Do not mutate inputs or expose cursor internals.

Preserve `list_recent`. Only change `src/events.py`, run the tests, and write the SlopBench receipt
with `python tools/write_slopbench_report.py`.
