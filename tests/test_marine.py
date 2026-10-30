"""Offshore stations: parsing NDBC feeds, the latest values and history."""

import calendar
import unittest
from pathlib import Path
from unittest import mock

from monitor import marine
from monitor.geo import in_region
from tests.support import DBTestCase

FIXTURES = Path(__file__).parent / "fixtures"
SEVEN_STONES = (FIXTURES / "ndbc_62107.txt").read_text()
E1 = (FIXTURES / "ndbc_62050.txt").read_text()
NOON = calendar.timegm((2026, 10, 30, 12, 0, 0))


class Parse(unittest.TestCase):
    def test_seven_stones(self):
        rows = marine.parse(SEVEN_STONES)
        self.assertEqual(rows[0]["ts"], NOON)
        self.assertEqual((rows[0]["wvht"], rows[0]["apd"], rows[0]["wtmp"], rows[0]["pres"]), (1.9, 10.0, 14.0, 1025.8))
        self.assertIsNone(rows[0]["wspd"])          # 'MM': not reported

    def test_e1_has_wind_but_no_waves(self):
        row = marine.parse(E1)[0]
        self.assertEqual((row["wdir"], row["wspd"]), (120.0, 7.7))
        self.assertIsNone(row["wvht"])

    def test_rejects_something_else(self):
        with self.assertRaises(ValueError):
            marine.parse("<html>Service unavailable</html>")

    def test_skips_broken_lines(self):
        text = SEVEN_STONES.splitlines()[:2] + ["2026 10 30 xx 00 MM", "2026 10 30 13 00 MM MM MM 2.1"]
        rows = marine.parse("\n".join(text))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["wvht"], 2.1)

    def test_every_station_is_in_cornish_waters(self):
        for sid, s in marine.STATIONS.items():
            self.assertTrue(in_region(s["lat"], s["lon"]), sid)


class Stored(DBTestCase):
    def poll(self):
        feeds = {"62107": marine.parse(SEVEN_STONES), "62050": marine.parse(E1)}
        with mock.patch.object(marine, "fetch", side_effect=lambda sid: feeds[sid]):
            return marine.poll(self.con)

    def test_poll_and_summary(self):
        n, errors = self.poll()
        self.assertEqual((n, errors), (len(marine.parse(SEVEN_STONES)) + len(marine.parse(E1)), []))
        out = {s["id"]: s for s in marine.summary(self.con, NOON + 600)["stations"]}
        self.assertEqual(out["62107"]["state"], "live")
        self.assertEqual(out["62107"]["wvht"], 1.9)
        self.assertNotIn("wspd", out["62107"])
        self.assertEqual(out["62050"]["wspd"], 7.7)
        self.assertEqual(marine.summary(self.con, NOON + 7 * 3600)["stations"][0]["state"], "stale")

    def test_a_failing_feed_leaves_the_others(self):
        def fetch(sid):
            if sid == "62050":
                raise OSError("timed out")
            return marine.parse(SEVEN_STONES)
        with mock.patch.object(marine, "fetch", side_effect=fetch):
            n, errors = marine.poll(self.con)
        self.assertGreater(n, 0)
        self.assertEqual(errors, ["62050: timed out"])

    def test_polling_twice_keeps_one_row_per_hour(self):
        self.poll()
        self.poll()
        count = self.con.execute("SELECT COUNT(*) FROM marine_obs WHERE station = '62050'").fetchone()[0]
        self.assertEqual(count, len(marine.parse(E1)))

    def test_history(self):
        self.poll()
        h = marine.history(self.con, "62050", 999, NOON + 600)
        self.assertEqual(h["days"], marine.MAX_DAYS)
        self.assertEqual(h["fields"][0], "wdir")
        self.assertEqual(h["readings"][-1][0], NOON)

    def test_a_station_never_heard_from(self):
        out = {s["id"]: s for s in marine.summary(self.con, NOON)["stations"]}
        self.assertEqual(out["62107"]["state"], "no-data")


if __name__ == "__main__":
    unittest.main()
