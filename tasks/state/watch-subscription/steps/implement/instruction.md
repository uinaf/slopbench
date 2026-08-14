Implement deterministic watch-subscription transitions in `src/watch.py`.

Preserve `is_active`. Add immutable `WatchState` and `Effect` values plus
`transition(state, event)`, which returns a new state and a tuple of effects
for an outer runtime to execute. It must not perform I/O.

States are `stopped`, `connecting`, and `active`. `subscribe` carries a
non-empty `topic`. From stopped it increments the generation, enters connecting,
and emits `connect`. Repeating the same topic while connecting or active is a
no-op. Changing topics emits `disconnect` for the old generation followed by
`connect` for a new generation. `connected` activates only a matching current
generation; stale events are no-ops. `unsubscribe` stops an active or connecting
watch and emits `disconnect`; from stopped it is a no-op.

Reject malformed states and events, unknown event kinds, and booleans used as
generations with `ValueError`. Do not mutate inputs.

Only change `src/watch.py`. Run the repository tests and write the required
SlopBench report with `python tools/write_slopbench_report.py`.
