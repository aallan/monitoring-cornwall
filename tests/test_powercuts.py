"""Power cuts: reading NGED's live file and keeping Cornwall's.

The fixture is five rows of NGED's Live Power Cuts file as published on
2 November 2026, under the NGED Open Data Licence: two in Cornwall, one near
Hayle with nobody yet confirmed off, one at Lynton in Devon and one near
Bristol.
"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from monitor import powercuts

CSV = (Path(__file__).parent / "fixtures" / "nged_power_cuts.csv").read_text()
UK = ZoneInfo("Europe/London")
PLACES = [{"name": "Looe", "place": "town", "lat": 50.354, "lon": -4.455},
          {"name": "Gunnislake", "place": "village", "lat": 50.522, "lon": -4.211},
          {"name": "Hayle", "place": "town", "lat": 50.186, "lon": -5.419}]


def uk(*args):
    return int(datetime(*args, tzinfo=UK).timestamp())


def cornish_test(lon, lat):
    """West of the Tamar, near enough, for these five."""
    return lon < -4.17


class Parse(unittest.TestCase):
    def setUp(self):
        self.cuts = {c["id"]: c for c in powercuts.parse(CSV)}

    def test_every_row_is_read(self):
        self.assertEqual(len(self.cuts), 5)

    def test_a_power_cut(self):
        c = self.cuts["INCD-19742-u"]
        self.assertEqual((c["lat"], c["lon"]), (50.34644, -4.48214))
        self.assertEqual((c["off"], c["predicted"], c["restored"]), (109, 0, 108))
        self.assertEqual((c["category"], c["status"], c["planned"]), ("HV OVERHEAD", "In Progress", False))
        self.assertEqual((c["start"], c["etr"]), (uk(2026, 11, 2, 12, 54), uk(2026, 11, 2, 17, 30)))
        self.assertEqual(len(c["postcodes"]), 13)   # 21 in the file, some of them twice
        self.assertTrue(all(p.startswith("PL13 ") for p in c["postcodes"]))

    def test_no_estimate_is_none(self):
        self.assertIsNone(self.cuts["INCD-19746-u"]["etr"])
        self.assertEqual(self.cuts["INCD-19746-u"]["postcodes"], ["PL18 9JF"])

    def test_upload_time(self):
        self.assertEqual(self.cuts["INCD-19742-u"]["uploaded"], uk(2026, 11, 2, 14, 55))

    def test_bad_rows_are_skipped(self):
        bad = CSV.replace("INCD-19674-u", "../x").replace("50.34644", "north")
        self.assertEqual(len(powercuts.parse(bad)), 3)

    def test_bad_values_are_tidied(self):
        text = CSV.replace("2026-11-02T17:30:00", "soon").replace("PL13 2DN", "<b>")
        c = {c["id"]: c for c in powercuts.parse(text)}["INCD-19742-u"]
        self.assertIsNone(c["etr"])
        self.assertNotIn("<b>", c["postcodes"])
        self.assertEqual(len(c["postcodes"]), 12)

    def test_a_byte_order_mark(self):
        self.assertEqual(len(powercuts.parse("﻿" + CSV)), 5)


class Cornish(unittest.TestCase):
    def setUp(self):
        self.kept = powercuts.cornish(powercuts.parse(CSV), cornish_test, PLACES)

    def test_only_cornwall_with_someone_off(self):
        self.assertEqual([c["id"] for c in self.kept], ["INCD-19742-u", "INCD-19746-u"])

    def test_named_after_the_nearest_place(self):
        self.assertEqual([c["place"] for c in self.kept], ["Looe", "Gunnislake"])

    def test_largest_first(self):
        self.assertEqual([c["off"] for c in self.kept], [109, 11])


class Poll(unittest.TestCase):
    def test_writes_the_summary(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(powercuts, "SUMMARY", Path(d) / "powercuts.json"), \
                mock.patch.object(powercuts.net, "fetch", return_value=CSV.encode()), \
                mock.patch.object(powercuts.overflows, "membership", return_value=cornish_test), \
                mock.patch.object(powercuts.ea, "load_places", return_value=PLACES):
            kept = powercuts.poll(now=uk(2026, 11, 2, 15, 0))
            summary = json.loads((Path(d) / "powercuts.json").read_text())
        self.assertEqual(len(kept), 2)
        self.assertEqual(summary["updated"], uk(2026, 11, 2, 14, 55))
        self.assertEqual(summary["source"], powercuts.DATASET)
        self.assertEqual([c["id"] for c in summary["power_cuts"]], ["INCD-19742-u", "INCD-19746-u"])
        self.assertNotIn("uploaded", summary["power_cuts"][0])

    def test_a_failed_fetch_leaves_the_last_summary(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "powercuts.json"
            path.write_text('{"power_cuts":[]}')
            with mock.patch.object(powercuts, "SUMMARY", path), \
                    mock.patch.object(powercuts.net, "fetch", side_effect=TimeoutError("timed out")):
                with self.assertRaises(TimeoutError):
                    powercuts.poll()
            self.assertEqual(path.read_text(), '{"power_cuts":[]}')


if __name__ == "__main__":
    unittest.main()
