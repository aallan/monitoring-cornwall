"""Flood warnings: times without a timezone, and which warnings are kept."""

import unittest
from unittest import mock

from monitor import floods
from tests.support import DBTestCase


class UKTime(unittest.TestCase):
    def test_summer_time(self):
        self.assertEqual(floods.uk_ts("2026-07-01T12:00:00"), floods.uk_ts("2026-07-01T11:00:00Z"))

    def test_winter_time(self):
        self.assertEqual(floods.uk_ts("2026-12-01T12:00:00"), floods.uk_ts("2026-12-01T12:00:00Z"))

    def test_an_explicit_offset_is_kept(self):
        self.assertEqual(floods.uk_ts("2026-07-01T12:00:00+00:00"), floods.uk_ts("2026-07-01T12:00:00Z"))

    def test_missing(self):
        self.assertIsNone(floods.uk_ts(None))


class PollWarnings(DBTestCase):
    def setUp(self):
        super().setUp()
        for aid, kind in (("112WAFTAMAR", "alert"), ("112FWFCAMEL", "warning")):
            self.con.execute("INSERT INTO flood_areas (id, kind, label, active, updated) VALUES (?, ?, ?, 1, 0)",
                             (aid, kind, aid.lower()))
        self.con.commit()

    def test_keeps_cornish_warnings_in_force_most_severe_first(self):
        items = [
            {"floodAreaID": "112WAFTAMAR", "severityLevel": 3, "description": "Tamar", "message": " Alert "},
            {"floodAreaID": "112FWFCAMEL", "severityLevel": 2, "description": "Camel",
             "timeSeverityChanged": "2026-10-30T09:00:00"},
            {"floodAreaID": "112WAFTAMAR", "severityLevel": 4},     # no longer in force
            {"floodAreaID": "DEVON", "severityLevel": 1},           # not one of Cornwall's areas
        ]
        with mock.patch.object(floods, "get", return_value={"items": items}), \
                mock.patch.object(floods, "fetch_outline", return_value=None):
            out = floods.poll_warnings(self.con)
        self.assertEqual([(w["id"], w["label"]) for w in out],
                         [("112FWFCAMEL", "Flood warning"), ("112WAFTAMAR", "Flood alert")])
        self.assertEqual(out[1]["message"], "Alert")
        self.assertEqual(out[0]["since"], floods.uk_ts("2026-10-30T09:00:00"))

    def test_a_failed_outline_still_shows_the_warning(self):
        items = [{"floodAreaID": "112FWFCAMEL", "severityLevel": 2}]
        with mock.patch.object(floods, "get", return_value={"items": items}), \
                mock.patch.object(floods, "fetch_outline", side_effect=OSError("timed out")):
            out = floods.poll_warnings(self.con)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["geometry"])

    def test_an_outline_is_fetched_once_and_kept(self):
        square = {"type": "MultiPolygon", "coordinates": [[[[-4.8, 50.5], [-4.7, 50.5], [-4.7, 50.6], [-4.8, 50.5]]]]}
        items = [{"floodAreaID": "112FWFCAMEL", "severityLevel": 2}]
        with mock.patch.object(floods, "get", return_value={"items": items}), \
                mock.patch.object(floods, "fetch_outline", return_value=square) as fetch:
            floods.poll_warnings(self.con)
            out = floods.poll_warnings(self.con)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(out[0]["geometry"], square)


if __name__ == "__main__":
    unittest.main()
