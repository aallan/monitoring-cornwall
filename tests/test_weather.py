"""Met Office weather warnings: parsing the feed and keeping Cornwall's.

The feed has no warnings in force as these tests are written, so the fixture
is built from items quoted word for word on the Home Assistant community forum
('UK MET Office Severe Weather Warnings', February 2024). Their links, guids
and publication times are made up in the feed's format. The amber wind
warning for Cornwall and Scilly is made up from the same template.
"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from monitor import weather

FEED = (Path(__file__).parent / "fixtures" / "met_warnings_sw.xml").read_bytes()
UK = ZoneInfo("Europe/London")


def uk(*args):
    return int(datetime(*args, tzinfo=UK).timestamp())


BEFORE = uk(2024, 2, 22, 2, 0)  # before any of them start


class Parse(unittest.TestCase):
    def setUp(self):
        self.items = weather.parse(FEED)

    def test_every_item_is_read(self):
        self.assertEqual(len(self.items), 4)
        self.assertEqual([w["level"] for w in self.items], ["Yellow", "Yellow", "Yellow", "Amber"])
        self.assertEqual([w["weather"] for w in self.items], ["rain", "wind", "rain", "wind"])

    def test_title_and_description_are_kept_as_published(self):
        w = self.items[2]
        self.assertEqual(w["title"], "Yellow warning of rain affecting South West England")
        self.assertTrue(w["description"].startswith("Yellow warning of rain affecting South West England: "))
        self.assertTrue(w["description"].endswith("valid from 0300 Thu 22 Feb to 1400 Thu 22 Feb"))
        self.assertTrue(w["link"].startswith("https://www.metoffice.gov.uk/weather/warnings-and-advice/"))
        self.assertTrue(w["image"].endswith("/yellow-rain.png"))

    def test_areas_split_on_commas_only(self):
        # 'Bournemouth Christchurch and Poole' is one authority, so 'and' isn't a separator.
        self.assertEqual(self.items[1]["areas"], ["Bournemouth Christchurch and Poole", "Dorset", "Wiltshire"])
        self.assertIn("Cornwall", self.items[2]["areas"])
        self.assertEqual(self.items[3]["areas"], ["Cornwall", "Isles of Scilly"])

    def test_validity_is_read_as_uk_time(self):
        w = self.items[2]
        self.assertEqual((w["from"], w["to"]), (uk(2024, 2, 22, 3, 0), uk(2024, 2, 22, 14, 0)))

    def test_british_summer_time(self):
        start, end = weather.valid_times("x: Cornwall valid from 0600 Sat 15 Jun to 2100 Sat 15 Jun",
                                         uk(2024, 6, 14, 12, 0))
        self.assertEqual(datetime.fromtimestamp(start, UK).utcoffset().total_seconds(), 3600)
        self.assertEqual(end - start, 15 * 3600)

    def test_a_warning_over_the_new_year(self):
        published = uk(2025, 12, 30, 10, 0)
        start, end = weather.valid_times("x: Cornwall valid from 1800 Wed 31 Dec to 0900 Thu 01 Jan", published)
        self.assertEqual((start, end), (uk(2025, 12, 31, 18, 0), uk(2026, 1, 1, 9, 0)))

    def test_published_in_january_for_late_december(self):
        start, _ = weather.valid_times("x: Cornwall valid from 1800 Wed 31 Dec to 0900 Thu 01 Jan",
                                       uk(2026, 1, 1, 6, 0))
        self.assertEqual(start, uk(2025, 12, 31, 18, 0))

    def test_no_validity_leaves_the_times_empty(self):
        self.assertEqual(weather.valid_times("Yellow warning of rain: Cornwall", BEFORE), (None, None))

    def test_items_that_arent_warnings_are_skipped(self):
        feed = FEED.replace(b"<title>Amber warning of wind", b"<title>Some other notice of wind")
        self.assertEqual(len(weather.parse(feed)), 3)

    def test_a_feed_with_a_dtd_is_refused(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel/></rss>'
        with self.assertRaises(ValueError):
            weather.parse(evil)

    def test_an_empty_feed(self):
        self.assertEqual(weather.parse(b"<rss version='2.0'><channel><title>x</title></channel></rss>"), [])


class Cornish(unittest.TestCase):
    def setUp(self):
        self.items = weather.parse(FEED)

    def test_only_warnings_naming_cornwall_or_scilly(self):
        kept = weather.cornish(self.items, BEFORE)
        self.assertEqual(len(kept), 2)
        self.assertTrue(all("Cornwall" in w["areas"] for w in kept))

    def test_most_severe_first(self):
        kept = weather.cornish(self.items, BEFORE)
        self.assertEqual([w["level"] for w in kept], ["Amber", "Yellow"])

    def test_scilly_alone_counts(self):
        item = dict(self.items[3], areas=["Isles of Scilly"])
        self.assertEqual(len(weather.cornish([item], BEFORE)), 1)

    def test_a_warning_that_has_ended_is_dropped(self):
        kept = weather.cornish(self.items, uk(2024, 2, 22, 15, 0))  # the yellow ended at 1400
        self.assertEqual([w["level"] for w in kept], ["Amber"])
        self.assertEqual(weather.cornish(self.items, uk(2024, 2, 22, 21, 0)), [])

    def test_a_warning_in_force_is_kept(self):
        self.assertEqual(len(weather.cornish(self.items, uk(2024, 2, 22, 13, 0))), 2)


class Poll(unittest.TestCase):
    def test_writes_the_summary(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(weather, "SUMMARY", Path(d) / "weather.json"), \
                mock.patch.object(weather.net, "fetch", return_value=FEED):
            kept = weather.poll(BEFORE)
            summary = json.loads((Path(d) / "weather.json").read_text())
        self.assertEqual(len(kept), 2)
        self.assertEqual(summary["generated"], BEFORE)
        self.assertEqual([w["level"] for w in summary["warnings"]], ["Amber", "Yellow"])

    def test_a_failed_fetch_leaves_the_last_summary(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "weather.json"
            path.write_text('{"generated":1,"warnings":[]}')
            with mock.patch.object(weather, "SUMMARY", path), \
                    mock.patch.object(weather.net, "fetch", side_effect=TimeoutError("timed out")):
                with self.assertRaises(TimeoutError):
                    weather.poll(BEFORE)
            self.assertEqual(path.read_text(), '{"generated":1,"warnings":[]}')


if __name__ == "__main__":
    unittest.main()
