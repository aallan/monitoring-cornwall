"""The poll cycle: every feed is polled, and filling gauge history comes last."""

import unittest
from unittest import mock

from monitor import poll
from tests.support import DBTestCase, QuietTestCase


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


class Marine(DBTestCase):
    def test_polls_straight_away_when_no_stations_are_known(self):
        # Polled a minute ago, but the station list is empty, as after an
        # upgrade that added it: poll now rather than in half an hour.
        poll.db.set_meta(self.con, "marine_refreshed", int(poll.time.time()) - 60)
        with mock.patch.object(poll.marine, "poll", return_value=(0, [])) as marine_poll, \
                mock.patch.object(poll.marine, "write_summary"):
            poll.poll_marine(self.con)
        marine_poll.assert_called_once()

    def test_waits_half_an_hour_once_stations_are_known(self):
        poll.db.set_meta(self.con, "marine_refreshed", int(poll.time.time()) - 60)
        poll.marine.upsert_station(self.con, "62107", "Seven Stones lightvessel", "ndbc", 50.102, -6.1, 0)
        with mock.patch.object(poll.marine, "poll", return_value=(0, [])) as marine_poll, \
                mock.patch.object(poll.marine, "write_summary"):
            poll.poll_marine(self.con)
        marine_poll.assert_not_called()
