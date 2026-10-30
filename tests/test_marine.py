"""Sea and coastal stations: the NDBC and CCO feeds, latest values and history."""

import calendar
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from monitor import marine
from monitor.geo import in_region
from tests.support import DBTestCase

FIXTURES = Path(__file__).parent / "fixtures"
SEVEN_STONES = (FIXTURES / "ndbc_62107.txt").read_text()
CCO_WAVES = json.loads((FIXTURES / "cco_waves.json").read_text())
CCO_MET = json.loads((FIXTURES / "cco_met.json").read_text())
CCO_LOOE_HISTORY = json.loads((FIXTURES / "cco_waves_looe_history.json").read_text())
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
        for sid, s in marine.NDBC.items():
            self.assertTrue(in_region(s["lat"], s["lon"]), sid)


class Stored(DBTestCase):
    def poll(self):
        feeds = {"62107": marine.parse(SEVEN_STONES), "62050": marine.parse(E1)}
        with mock.patch.object(marine, "fetch", side_effect=lambda sid: feeds[sid]), \
                mock.patch.dict(os.environ, {"CCO_API_KEY": ""}):
            return marine.poll(self.con, NOON)

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
        with mock.patch.object(marine, "fetch", side_effect=fetch), mock.patch.dict(os.environ, {"CCO_API_KEY": ""}):
            n, errors = marine.poll(self.con, NOON)
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
        marine.upsert_station(self.con, "62107", "Seven Stones lightvessel", "ndbc", 50.102, -6.1, NOON)
        out = {s["id"]: s for s in marine.summary(self.con, NOON)["stations"]}
        self.assertEqual(out["62107"]["state"], "no-data")

    def test_an_unknown_station_has_no_history(self):
        self.assertIsNone(marine.history(self.con, "99999", 7, NOON))


# A stand-in for the county test: west of the Tamar and south of Bude.
def cornish(lon, lat):
    return lon < -4.2 and lat < 50.95


class CoastalParse(unittest.TestCase):
    def test_time(self):
        self.assertEqual(marine.cco_time("20261030#140000"), calendar.timegm((2026, 10, 30, 14, 0, 0)))
        self.assertIsNone(marine.cco_time(None))
        self.assertIsNone(marine.cco_time("2026-10-30"))

    def test_ids(self):
        self.assertEqual(marine.cco_id("St Mary's Sound"), "cco-st-mary-s-sound")
        self.assertEqual(marine.cco_id("Looe Bay"), "cco-looe-bay")

    def test_rows(self):
        looe = next(f for f in CCO_WAVES["features"] if f["properties"]["sensor"] == "Looe Bay")
        row = marine.cco_row("waves", looe["properties"])
        self.assertEqual(row["ts"], marine.cco_time("20261030#140000"))
        self.assertEqual(set(row) - {"ts"}, {"wvht", "dpd", "apd", "mwd", "wtmp"})
        self.assertIsInstance(row["wvht"], float)
        bude = next(f for f in CCO_MET["features"] if f["properties"]["sensor"] == "Bude")
        self.assertEqual(set(marine.cco_row("met", bude["properties"])) - {"ts"}, {"wspd", "gst", "wdir", "atmp", "pres"})

    def test_sites_keep_cornish_sensors_that_have_reported_this_month(self):
        now = marine.cco_time("20261030#150000")
        sites = marine.cco_sites({"waves": CCO_WAVES, "met": CCO_MET}, cornish, now)
        # Bideford Bay and Exmouth are in Devon; Wave Hub last reported in
        # 2018, Penzance's weather station in March, and the Isles of Scilly
        # buoy never.
        self.assertEqual(sorted(sites), ["Bude", "Looe Bay", "Perranporth", "Port Isaac"])
        self.assertEqual(sorted(sites["Looe Bay"]["kinds"]), ["met", "waves"])
        buoy = next(f for f in CCO_WAVES["features"] if f["properties"]["sensor"] == "Looe Bay")
        self.assertEqual([sites["Looe Bay"]["lon"], sites["Looe Bay"]["lat"]],
                         [round(c, 5) for c in buoy["geometry"]["coordinates"]])   # at the buoy, not the weather station
        for site in sites.values():
            self.assertTrue(in_region(site["lat"], site["lon"]))


class CoastalPoll(DBTestCase):
    NOW = marine.cco_time("20261030#150000")

    def get(self, calls):
        def cco_get(kind, key, params=None):
            calls.append((kind, key, params))
            if params:
                return CCO_LOOE_HISTORY if kind == "waves" else {"features": []}
            return CCO_WAVES if kind == "waves" else CCO_MET
        return cco_get

    def test_first_poll_fetches_a_weeks_history(self):
        calls = []
        with mock.patch.object(marine, "cco_get", side_effect=self.get(calls)), \
                mock.patch.object(marine.overflows, "membership", return_value=cornish):
            marine.poll_cco(self.con, "key", self.NOW)
        history = [c for c in calls if c[2]]
        self.assertTrue(all(c[1] == "key" for c in calls))
        self.assertTrue(all(c[2]["duration"] == marine.CCO_HISTORY_HOURS for c in history))
        out = {s["id"]: s for s in marine.summary(self.con, self.NOW)["stations"]}
        self.assertEqual(sorted(out), ["cco-bude", "cco-looe-bay", "cco-perranporth", "cco-port-isaac"])
        self.assertEqual(out["cco-looe-bay"]["source"], "cco")
        rows = self.con.execute("SELECT COUNT(*) FROM marine_obs WHERE station = 'cco-looe-bay' AND wvht IS NOT NULL").fetchone()[0]
        self.assertEqual(rows, len(CCO_LOOE_HISTORY["features"]))

    def test_a_later_poll_fetches_only_the_gap(self):
        calls = []
        with mock.patch.object(marine, "cco_get", side_effect=self.get(calls)), \
                mock.patch.object(marine.overflows, "membership", return_value=cornish):
            marine.poll_cco(self.con, "key", self.NOW)
            calls.clear()
            marine.poll_cco(self.con, "key", self.NOW)
        # Looe Bay's buoy history is already held up to its latest reading.
        self.assertFalse([c for c in calls if c[2] and c[2]["sensor"] == "98"])

    def test_buoy_and_weather_rows_for_the_same_time_merge(self):
        ts = marine.cco_time("20261030#140000")
        marine.store(self.con, "cco-x", [{"ts": ts, "wvht": 1.2}])
        marine.store(self.con, "cco-x", [{"ts": ts, "wspd": 5.0}])
        row = self.con.execute("SELECT wvht, wspd FROM marine_obs WHERE station = 'cco-x'").fetchone()
        self.assertEqual(tuple(row), (1.2, 5.0))

    def test_no_key_no_coastal_stations(self):
        with mock.patch.object(marine, "fetch", side_effect=OSError("offline")), \
                mock.patch.object(marine, "poll_cco") as poll_cco, mock.patch.dict(os.environ, {"CCO_API_KEY": ""}):
            marine.poll(self.con, self.NOW)
        poll_cco.assert_not_called()


if __name__ == "__main__":
    unittest.main()


TIDES_LATEST = json.loads((FIXTURES / "cco_tides_latest.json").read_text())
TIDES_PORT_ISAAC = json.loads((FIXTURES / "cco_tides_port_isaac.json").read_text())


class Tides(DBTestCase):
    NOW = marine.cco_time("20261030#150000")

    def get(self, calls):
        def cco_get(kind, key, params=None, when="latest"):
            calls.append((kind, params, when))
            return TIDES_PORT_ISAAC if params else TIDES_LATEST
        return cco_get

    def poll(self, calls, now=None):
        with mock.patch.object(marine, "cco_get", side_effect=self.get(calls)), \
                mock.patch.object(marine.overflows, "membership", return_value=cornish):
            return marine.poll_tides(self.con, "key", now or self.NOW)

    def test_events_from_a_run_of_predictions(self):
        events = marine.tide_events(TIDES_PORT_ISAAC["features"])
        self.assertEqual(len(events), 9)
        self.assertEqual(events[0], (marine.cco_time("20261030#200900"), "high", 7.076))
        self.assertEqual([k for _, k, _ in events[:4]], ["high", "low", "high", "low"])

    def test_poll_stores_port_isaac_and_skips_empty_sites(self):
        calls = []
        self.assertEqual(self.poll(calls), 9)
        # Newlyn and Devonport are listed without a reading, so only Port
        # Isaac's predictions are asked for, 48 hours ahead.
        ahead = [c for c in calls if c[1]]
        self.assertEqual(len(ahead), 1)
        self.assertEqual(ahead[0][1], {"sensor": "102", "duration": marine.TIDE_AHEAD})
        self.assertEqual(ahead[0][2], "20261101150000")
        out = {s["id"]: s for s in marine.summary(self.con, self.NOW)["stations"]}
        self.assertEqual(out["cco-port-isaac"]["tides"][0], [marine.cco_time("20261030#200900"), "high", 7.076])
        self.assertEqual(len(out["cco-port-isaac"]["tides"]), marine.TIDE_EVENTS_SHOWN)

    def test_no_new_request_while_two_days_are_held(self):
        self.poll([])
        calls = []
        self.poll(calls, self.NOW + 3600)
        self.assertFalse([c for c in calls if c[1]])
        calls = []
        self.poll(calls, self.NOW + 20 * 3600)   # under 36 hours left: fetch again
        self.assertTrue([c for c in calls if c[1]])

    def test_only_future_tides_are_shown(self):
        self.poll([])
        later = marine.cco_time("20261031#090000")
        tides = {s["id"]: s for s in marine.summary(self.con, later)["stations"]}["cco-port-isaac"]["tides"]
        self.assertTrue(all(t[0] > later for t in tides))
