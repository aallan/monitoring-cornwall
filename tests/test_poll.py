"""The poll cycle: every feed is polled, and filling gauge history comes last."""

import unittest
from unittest import mock

from monitor import poll
from tests.support import QuietTestCase


class Cycle(QuietTestCase):
    def run_cycle(self, **failures):
        order = []

        def step(name, result=None):
            def run(*args):
                order.append(name)
                if name in failures:
                    raise failures[name]
                return result
            return run

        with mock.patch.object(poll.db, "connect", return_value=mock.MagicMock()), \
                mock.patch.object(poll, "poll_gauges", side_effect=step("gauges", ({"m": 1}, {}))), \
                mock.patch.object(poll, "poll_overflows", side_effect=step("overflows")), \
                mock.patch.object(poll, "poll_floods", side_effect=step("floods")), \
                mock.patch.object(poll, "poll_bathing", side_effect=step("bathing")), \
                mock.patch.object(poll, "poll_marine", side_effect=step("marine")), \
                mock.patch.object(poll, "fill_gauge_gaps", side_effect=step("fill")):
            poll.cycle()
        return order

    def test_filling_gauge_history_comes_last(self):
        self.assertEqual(self.run_cycle(), ["gauges", "overflows", "floods", "bathing", "marine", "fill"])

    def test_a_failing_feed_doesnt_stop_the_others(self):
        order = self.run_cycle(overflows=TimeoutError("no complete answer within 120 s"))
        self.assertEqual(order, ["gauges", "overflows", "floods", "bathing", "marine", "fill"])

    def test_no_history_fill_when_the_gauges_failed(self):
        order = self.run_cycle(gauges=TimeoutError("timed out"))
        self.assertEqual(order, ["gauges", "overflows", "floods", "bathing", "marine"])


if __name__ == "__main__":
    unittest.main()
