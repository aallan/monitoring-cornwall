"""Bathing waters: parsing the Environment Agency's list, the summary and samples."""

import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from monitor import bathing
from monitor.floods import uk_ts
from tests.support import NOW, DBTestCase

FIXTURES = Path(__file__).parent / "fixtures"
LIST = json.loads((FIXTURES / "bathing_list.json").read_text())
SAMPLES = json.loads((FIXTURES / "bathing_samples.json").read_text())


def by_name(name):
    return next(i for i in LIST["result"]["items"] if bathing.value(i["name"]) == name)


class Parse(unittest.TestCase):
    def test_a_bathing_water(self):
        w = bathing.parse(by_name("Polzeath"))
        self.assertEqual((w["id"], w["eubwid"], w["name"]), ("33300", "ukk3104-33300", "Polzeath"))
        self.assertEqual((w["lat"], w["lon"]), (50.57481, -4.91644))
        self.assertEqual((w["classification"], w["class_year"]), ("Excellent", 2025))
        self.assertEqual(w["risk"], "normal")
        self.assertEqual(w["risk_expires"], uk_ts("2026-10-02T08:29:00"))
        self.assertEqual((w["sample_ts"], w["ec"], w["ec_q"], w["ie"]), (uk_ts("2026-09-23T08:57:00"), 10.0, "<", 54.0))
        self.assertEqual(w["rain_affected"], 1)

    def test_every_fixture_lands_in_cornish_waters(self):
        for item in LIST["result"]["items"]:
            w = bathing.parse(item)
            self.assertTrue(49.9 < w["lat"] < 51.0 and -6.5 < w["lon"] < -4.1, w["name"])

    def test_a_position_outside_the_region_is_dropped(self):
        item = copy.deepcopy(by_name("Kingsand"))
        item["samplingPoint"]["lat"] = 54.0
        self.assertIsNone(bathing.parse(item))

    def test_a_missing_or_odd_sampling_point_is_dropped(self):
        item = copy.deepcopy(by_name("Kingsand"))
        item["samplingPoint"]["samplePointNotation"] = "../x"
        self.assertIsNone(bathing.parse(item))
        del item["samplingPoint"]
        self.assertIsNone(bathing.parse(item))

    def test_linked_data_values(self):
        self.assertEqual(bathing.value({"_value": "x"}), "x")
        self.assertEqual(bathing.value([{"_value": "x"}, {"_value": "y"}]), "x")
        self.assertEqual(bathing.value({"_about": "http://x"}), "http://x")
        self.assertIsNone(bathing.field({"a": {"b": None}}, "a.b.c"))
        self.assertEqual(bathing.year_of("http://x/compliance-rBWD/point/1/year/2025"), 2025)


class Summary(DBTestCase):
    def refresh(self, now=NOW):
        with mock.patch.object(bathing, "get_json", return_value=LIST):
            return bathing.refresh(self.con, now)

    def test_refresh_and_summary(self):
        self.assertEqual(self.refresh(), 2)
        out = {w["name"]: w for w in bathing.summary(self.con, NOW)["bathing_waters"]}
        self.assertEqual(out["Kingsand"]["classification"], "Good")
        self.assertTrue(out["Polzeath"]["rain_affected"])
        self.assertNotIn("rain_affected", out["Kingsand"])
        self.assertEqual(out["Polzeath"]["sample"]["ec_q"], "<")

    def test_an_expired_forecast_is_not_shown(self):
        self.refresh()
        expires = uk_ts("2026-10-02T08:29:00")
        before = bathing.summary(self.con, expires - 60)["bathing_waters"]
        after = bathing.summary(self.con, expires + 60)["bathing_waters"]
        self.assertTrue(all(w.get("risk") == "normal" for w in before))
        self.assertTrue(all("risk" not in w for w in after))

    def test_a_bathing_water_dropped_from_the_list_is_hidden(self):
        self.refresh()
        smaller = {"result": {"items": [by_name("Polzeath")]}}
        with mock.patch.object(bathing, "get_json", return_value=smaller):
            bathing.refresh(self.con, NOW)
        self.assertEqual([w["name"] for w in bathing.summary(self.con, NOW)["bathing_waters"]], ["Polzeath"])


class Samples(DBTestCase):
    def test_samples_oldest_first(self):
        with mock.patch.object(bathing, "get_json", return_value=SAMPLES) as get:
            rows = bathing.samples("33300")["samples"]
        self.assertEqual([r[0] for r in rows], sorted(r[0] for r in rows))
        self.assertEqual(rows[-1], [uk_ts("2026-09-23T08:57:00"), 10.0, "<", 54.0, "="])
        params = get.call_args.args[1]
        self.assertEqual(params["bwq_samplingPoint"], bathing.SAMPLING_POINT.format("33300"))
        self.assertEqual(params["_sort"], "-sampleDateTime")


if __name__ == "__main__":
    unittest.main()
