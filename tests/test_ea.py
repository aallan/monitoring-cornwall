"""Environment Agency client: choosing a station's series, and storing readings."""

import time
import unittest
from unittest import mock

from monitor import ea
from tests.support import DBTestCase, measure_item


class PickMeasure(unittest.TestCase):
    def test_prefers_the_series_still_reporting(self):
        # Gunnislake lists its old stage series beside the one that replaced it.
        station = {"measures": [measure_item("47117-level-stage-i-15_min-m", unit="m"),
                                measure_item("47117-level-stage-i-15_min-mASD", unit="mASD")]}
        self.assertEqual(ea.pick_measure(station, "level", frozenset({"47117-level-stage-i-15_min-mASD"})),
                         ("level", "47117-level-stage-i-15_min-mASD", "mASD"))

    def test_falls_back_to_unit_order_when_nothing_is_known_to_report(self):
        station = {"measures": [measure_item("x-mAOD", unit="mAOD"), measure_item("x-m", unit="m")]}
        self.assertEqual(ea.pick_measure(station, "level"), ("level", "x-m", "m"))

    def test_tidal_gauge(self):
        station = {"measures": [measure_item("w-m", "Tidal Level", "m"),
                                measure_item("w-mAOD", "Tidal Level", "mAOD")]}
        self.assertEqual(ea.pick_measure(station, "level"), ("tidal", "w-mAOD", "mAOD"))
        self.assertEqual(ea.pick_measure(station, "level", frozenset({"w-m"})), ("tidal", "w-m", "m"))

    def test_stage_wins_over_tidal(self):
        station = {"measures": [measure_item("t", "Tidal Level", "mAOD"), measure_item("s", "Stage", "m")]}
        self.assertEqual(ea.pick_measure(station, "level")[0], "level")

    def test_ignores_other_periods_and_qualifiers(self):
        station = {"measures": [measure_item("hourly", period=3600),
                                measure_item("sump", qualifier="Sump Level")]}
        self.assertIsNone(ea.pick_measure(station, "level"))

    def test_rain_gauge(self):
        station = {"measures": measure_item("r-15", qualifier=None, unit="mm", parameter="rainfall")}
        self.assertEqual(ea.pick_measure(station, "rainfall"), ("rain", "r-15", "mm"))


class ReportingMeasures(unittest.TestCase):
    def test_keeps_series_with_a_reading_in_the_last_week(self):
        recent = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
        items = [
            {"measure": "http://x/id/measures/live", "dateTime": recent},
            {"measure": "http://x/id/measures/dead", "dateTime": "2026-06-16T08:30:00Z"},
            {"measure": "http://x/id/measures/undated"},
            {"dateTime": recent},
        ]
        with mock.patch.object(ea, "get", return_value={"items": items}):
            self.assertEqual(ea.reporting_measures("level"), frozenset({"live"}))


class Helpers(unittest.TestCase):
    def test_in_service(self):
        self.assertTrue(ea.in_service({"status": "http://x/def/core/statusActive"}))
        self.assertTrue(ea.in_service({}))
        self.assertFalse(ea.in_service({"status": "http://x/def/core/statusClosed"}))
        self.assertFalse(ea.in_service({"status": "http://x/def/core/statusSuspended"}))

    def test_coords(self):
        self.assertEqual(ea.coords({"lat": 50.5, "long": -4.2}), (50.5, -4.2))
        self.assertEqual(ea.coords({"lat": [50.5, 50.6], "long": [-4.2, -4.3]}), (50.5, -4.2))
        self.assertIsNone(ea.coords({"lat": 54.0, "long": -2.0}))      # Cumbria
        self.assertIsNone(ea.coords({"lat": None, "long": None}))

    def test_tidy_name(self):
        self.assertEqual(ea.tidy_name("CANWORTHY  WATER BRIDGE"), "Canworthy Water Bridge")
        self.assertEqual(ea.tidy_name("St Erth"), "St Erth")

    def test_number(self):
        self.assertEqual(ea.number(1.5), 1.5)
        self.assertEqual(ea.number([None, 2, 3]), 2.0)
        self.assertIsNone(ea.number("1.5"))
        self.assertIsNone(ea.number([]))

    def test_timestamps_round_trip(self):
        self.assertEqual(ea.to_iso(ea.to_ts("2026-10-30T12:30:00Z")), "2026-10-30T12:30:00Z")

    def test_nearest_place_prefers_villages_over_close_hamlets(self):
        places = [{"name": "Village", "place": "village", "lat": 50.40, "lon": -4.90},
                  {"name": "Hamlet", "place": "hamlet", "lat": 50.4045, "lon": -4.90}]
        # The hamlet is a little nearer, but not twice as near.
        self.assertEqual(ea.nearest_place(50.4025, -4.90, places), "Village")
        self.assertEqual(ea.nearest_place(50.4045, -4.90, places), "Hamlet")
        self.assertIsNone(ea.nearest_place(50.4, -4.9, []))


class StoreLatest(DBTestCase):
    def test_stores_only_our_stations(self):
        ours = self.add_station("A")
        items = [
            {"measure": f"http://x/id/measures/{ours}", "dateTime": "2026-10-30T12:00:00Z", "value": [0.41, 0.41]},
            {"measure": "http://x/id/measures/someone-else", "dateTime": "2026-10-30T12:00:00Z", "value": 9.9},
            {"measure": f"http://x/id/measures/{ours}", "dateTime": None, "value": 1.0},
        ]
        with mock.patch.object(ea, "get", side_effect=[{"items": items}, {"items": []}]):
            latest, held = ea.store_latest(self.con)
        self.assertEqual(latest, {ours: ea.to_ts("2026-10-30T12:00:00Z")})
        self.assertEqual(held, {})
        rows = self.con.execute("SELECT measure, value FROM readings").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(ours, 0.41)])


if __name__ == "__main__":
    unittest.main()
