Implement a deterministic sync command transition in `src/sync.py`.

`transition(state, command)` returns the next immutable `SyncState` and a tuple
of `Effect` values for an outer runtime to execute. It must never perform I/O.

Valid states are `idle`, `syncing`, and `failed`. A `start` command from idle
or failed increments the generation, enters syncing, clears the prior error,
and emits one `upload` effect for that generation. Starting while already
syncing is a no-op.

`succeeded` and `failed` commands carry a generation. Apply them only while
syncing and only when their generation matches the current generation; stale
completions are no-ops. A matching success returns to idle. A matching failure
enters failed and records its non-empty `message`. `cancel` returns a syncing
state to idle and emits one `cancel` effect; otherwise it is a no-op.

Reject malformed states and commands, booleans used as generations, unknown
commands, and invalid messages with `ValueError`. Do not mutate inputs.

Only change `src/sync.py`. Run the repository tests and write the required
SlopBench report with `python tools/write_slopbench_report.py`.
