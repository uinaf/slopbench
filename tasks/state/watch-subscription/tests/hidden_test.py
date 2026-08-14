from __future__ import annotations

import os
import unittest

from src.watch import Effect, WatchState, transition


class ImplementContract(unittest.TestCase):
    def test_stale_connected_event_is_ignored(self) -> None:
        current = WatchState("connecting", "orders", 4, 0)
        self.assertEqual(
            transition(current, {"kind": "connected", "generation": 3}),
            (current, ()),
        )

    def test_same_topic_is_idempotent_and_topic_change_is_ordered(self) -> None:
        active = WatchState("active", "orders", 2, 0)
        self.assertEqual(
            transition(active, {"kind": "subscribe", "topic": "orders"}),
            (active, ()),
        )
        self.assertEqual(
            transition(active, {"kind": "subscribe", "topic": "billing"}),
            (
                WatchState("connecting", "billing", 3, 0),
                (
                    Effect("disconnect", 2, "orders"),
                    Effect("connect", 3, "billing"),
                ),
            ),
        )

    def test_invalid_states_and_events_are_rejected(self) -> None:
        cases = [
            (WatchState("mystery", None, 0, 0), {"kind": "unsubscribe"}),
            (WatchState("active", None, 1, 0), {"kind": "unsubscribe"}),
            (WatchState("stopped", "orders", 0, 0), {"kind": "unsubscribe"}),
            (WatchState("stopped", None, True, 0), {"kind": "unsubscribe"}),
            (WatchState(), {"kind": "subscribe", "topic": ""}),
            (WatchState(), {"kind": "connected", "generation": True}),
            (WatchState(), {"kind": "unknown"}),
        ]
        for state, event in cases:
            with self.subTest(state=state, event=event), self.assertRaises(ValueError):
                transition(state, event)


class ReviewContract(ImplementContract):
    def test_reconnect_backoff_and_generation_progression(self) -> None:
        active = WatchState("active", "orders", 3, 0)
        waiting, effects = transition(active, {"kind": "connection_lost", "generation": 3})
        self.assertEqual(waiting, WatchState("waiting", "orders", 3, 1))
        self.assertEqual(effects, (Effect("schedule_retry", 3, "orders", 1),))
        connecting, effects = transition(waiting, {"kind": "retry_due", "generation": 3})
        self.assertEqual(connecting, WatchState("connecting", "orders", 4, 1))
        self.assertEqual(effects, (Effect("connect", 4, "orders"),))
        waiting, effects = transition(connecting, {"kind": "connection_lost", "generation": 4})
        self.assertEqual(waiting.retry_count, 2)
        self.assertEqual(effects, (Effect("schedule_retry", 4, "orders", 2),))
        capped = WatchState("active", "orders", 9, 4)
        _, effects = transition(capped, {"kind": "connection_lost", "generation": 9})
        self.assertEqual(effects, (Effect("schedule_retry", 9, "orders", 8),))

    def test_stale_reconnect_events_are_ignored(self) -> None:
        waiting = WatchState("waiting", "orders", 5, 2)
        self.assertEqual(
            transition(waiting, {"kind": "retry_due", "generation": 4}),
            (waiting, ()),
        )
        self.assertEqual(
            transition(waiting, {"kind": "connected", "generation": 5}),
            (waiting, ()),
        )
        active = WatchState("active", "orders", 5, 2)
        self.assertEqual(
            transition(active, {"kind": "connection_lost", "generation": 4}),
            (active, ()),
        )

    def test_waiting_can_be_cancelled_or_replaced(self) -> None:
        waiting = WatchState("waiting", "orders", 5, 2)
        self.assertEqual(
            transition(waiting, {"kind": "subscribe", "topic": "orders"}),
            (waiting, ()),
        )
        self.assertEqual(
            transition(waiting, {"kind": "unsubscribe"}),
            (
                WatchState("stopped", None, 5, 0),
                (Effect("cancel_retry", 5, "orders"),),
            ),
        )
        self.assertEqual(
            transition(waiting, {"kind": "subscribe", "topic": "billing"}),
            (
                WatchState("connecting", "billing", 6, 0),
                (
                    Effect("cancel_retry", 5, "orders"),
                    Effect("connect", 6, "billing"),
                ),
            ),
        )

    def test_success_resets_retry_count_and_old_behavior_remains(self) -> None:
        connecting = WatchState("connecting", "orders", 6, 3)
        active, effects = transition(connecting, {"kind": "connected", "generation": 6})
        self.assertEqual(active, WatchState("active", "orders", 6, 0))
        self.assertEqual(effects, ())
        self.assertEqual(
            transition(active, {"kind": "subscribe", "topic": "orders"}),
            (active, ()),
        )


class HardenContract(ReviewContract):
    def test_retryable_failures_are_bounded(self) -> None:
        from src.watch import BoundaryEvent

        connecting = WatchState("connecting", "orders", 7, 2)
        waiting, effects = transition(
            connecting,
            {
                "kind": "connection_failed",
                "generation": 7,
                "error_kind": "rate_limit",
            },
        )
        self.assertEqual(waiting, WatchState("waiting", "orders", 7, 3))
        self.assertEqual(effects, (Effect("schedule_retry", 7, "orders", 4),))

        exhausted = WatchState("connecting", "orders", 11, 5)
        failed, effects = transition(
            exhausted,
            {
                "kind": "connection_failed",
                "generation": 11,
                "error_kind": "transient",
            },
        )
        self.assertEqual(failed, WatchState("failed", "orders", 11, 5, "transient"))
        self.assertEqual(
            effects,
            (
                Effect(
                    "emit_event",
                    11,
                    "orders",
                    event=BoundaryEvent(
                        event="watch_failed",
                        operation="watch.connect",
                        resource_id="orders",
                        outcome="failed",
                        error_kind="transient",
                    ),
                ),
            ),
        )

    def test_only_rate_limit_and_transient_failures_retry(self) -> None:
        from src.watch import BoundaryEvent

        connecting = WatchState("connecting", "orders", 6, 0)
        for error_kind in ("validation", "auth", "conflict", "internal", "unknown"):
            with self.subTest(error_kind=error_kind):
                failed, effects = transition(
                    connecting,
                    {
                        "kind": "connection_failed",
                        "generation": 6,
                        "error_kind": error_kind,
                    },
                )
                self.assertEqual(failed, WatchState("failed", "orders", 6, 0, error_kind))
                self.assertEqual(
                    effects,
                    (
                        Effect(
                            "emit_event",
                            6,
                            "orders",
                            event=BoundaryEvent(
                                event="watch_failed",
                                operation="watch.connect",
                                resource_id="orders",
                                outcome="failed",
                                error_kind=error_kind,
                            ),
                        ),
                    ),
                )

    def test_terminal_event_has_stable_redacted_fields_and_is_exact_once(self) -> None:
        from src.watch import BoundaryEvent

        connecting = WatchState("connecting", "orders", 4, 0)
        failure = {
            "kind": "connection_failed",
            "generation": 4,
            "error_kind": "auth",
            "message": "temporary rate limit token=top-secret",
            "payload": {"token": "top-secret", "body": "full-payload"},
        }
        failed, effects = transition(connecting, failure)
        self.assertEqual(failed, WatchState("failed", "orders", 4, 0, "auth"))
        self.assertEqual(
            effects,
            (
                Effect(
                    "emit_event",
                    4,
                    "orders",
                    event=BoundaryEvent(
                        event="watch_failed",
                        operation="watch.connect",
                        resource_id="orders",
                        outcome="failed",
                        error_kind="auth",
                    ),
                ),
            ),
        )
        self.assertNotIn("top-secret", repr(effects))
        self.assertNotIn("full-payload", repr(effects))
        self.assertEqual(transition(failed, failure), (failed, ()))

    def test_connection_loss_exhaustion_and_failed_recovery(self) -> None:
        from src.watch import BoundaryEvent

        exhausted = WatchState("active", "orders", 8, 5)
        failed, effects = transition(exhausted, {"kind": "connection_lost", "generation": 8})
        self.assertEqual(failed, WatchState("failed", "orders", 8, 5, "transient"))
        self.assertEqual(
            effects,
            (
                Effect(
                    "emit_event",
                    8,
                    "orders",
                    event=BoundaryEvent(
                        event="watch_failed",
                        operation="watch.connect",
                        resource_id="orders",
                        outcome="failed",
                        error_kind="transient",
                    ),
                ),
            ),
        )

        connecting, effects = transition(failed, {"kind": "subscribe", "topic": "orders"})
        self.assertEqual(connecting, WatchState("connecting", "orders", 9, 0))
        self.assertEqual(effects, (Effect("connect", 9, "orders"),))
        self.assertEqual(
            transition(failed, {"kind": "unsubscribe"}),
            (WatchState("stopped", None, 8, 0), ()),
        )

    def test_failure_kind_is_validated(self) -> None:
        connecting = WatchState("connecting", "orders", 2, 0)
        for error_kind in (None, "timeout", True):
            with self.subTest(error_kind=error_kind), self.assertRaises(ValueError):
                transition(
                    connecting,
                    {
                        "kind": "connection_failed",
                        "generation": 2,
                        "error_kind": error_kind,
                    },
                )


def main() -> int:
    phase = os.environ.get("SLOPBENCH_PHASE")
    cases = {
        "implement": ImplementContract,
        "review": ReviewContract,
        "harden": HardenContract,
    }
    try:
        case = cases[phase]
    except KeyError as exc:
        raise SystemExit(f"unknown SLOPBENCH_PHASE: {phase}") from exc
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(case)
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
