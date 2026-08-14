Watch subscriptions now reconnect after a connection is lost.

Add a `waiting` state. A matching `connection_lost` event from active or
connecting enters waiting, increments `retry_count`, and emits `schedule_retry`
with delays of 1, 2, 4, then at most 8 seconds. A matching `retry_due` event
from waiting increments the generation, enters connecting, and emits `connect`.
Stale loss, retry, and connected events remain no-ops. A successful connection
resets `retry_count` to zero.

Unsubscribing while waiting emits `cancel_retry` and stops. Subscribing to a
different topic while waiting emits `cancel_retry` followed by `connect` for a
new generation; subscribing to the same topic remains a no-op. Preserve every
prior transition. Effects remain data and `transition` must not perform I/O.

Only change `src/watch.py`. Run the repository tests and rewrite the SlopBench
report with `python tools/write_slopbench_report.py`.
