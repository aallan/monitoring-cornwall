"""Drought status: finding the latest weekly report and reading Cornwall's.

The fixtures are trimmed from GOV.UK's answers on 2 November 2026: the
search for the series, the series with its four latest reports, and the
report for 23 to 29 October 2026 cut to its sections on hosepipe bans and
drought status. They're Crown copyright, under the Open Government Licence.
"""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from monitor import drought

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH = json.loads((FIXTURES / "drought_search.json").read_text())
SERIES = json.loads((FIXTURES / "drought_series.json").read_text())
REPORT = json.loads((FIXTURES / "drought_report.json").read_text())
BODY = REPORT["details"]["body"]


class Parse(unittest.TestCase):
    def test_cornwall_is_in_two_stages(self):
        s = drought.parse(BODY)
        self.assertEqual([x["stage"] for x in s["stages"]], ["Severe drought", "Drought"])
        self.assertTrue(s["stages"][0]["text"].endswith("and Stithians and Drift reservoirs"))
        self.assertEqual(s["stages"][1]["text"],
                         "Remaining parts of Devon, Cornwall and Isles of Scilly that are not in severe drought")

    def test_south_west_waters_ban_only(self):
        self.assertEqual(drought.parse(BODY)["bans"], ["South West Water – whole company area, excluding Bournemouth"])

    def test_text_is_kept_as_published(self):
        text = drought.parse(BODY)["stages"][0]["text"]
        self.assertIn("Kenninck, Tottisford & Trenchford (KTT)", text)   # entities and abbreviations
        self.assertNotIn("  ", text)

    def test_a_stage_given_as_a_paragraph(self):
        body = BODY.replace("<p>Northeast</p>", "<p>Devon, Cornwall and Isles of Scilly</p>")
        stages = drought.parse(body)["stages"]
        self.assertIn({"stage": "Prolonged dry weather", "text": "Devon, Cornwall and Isles of Scilly"}, stages)

    def test_out_of_drought(self):
        body = (BODY.replace("Cornwall", "Elsewhere").replace("Isles of Scilly", "Elsewhere")
                .replace("South West Water –", "Another Water –"))
        self.assertEqual(drought.parse(body), {"stages": [], "bans": []})

    def test_a_report_without_stages_is_refused(self):
        body = BODY.replace('id="environment-agency-areas-in-', 'id="something-else-')
        with self.assertRaises(ValueError):
            drought.parse(body)


class Finding(unittest.TestCase):
    def test_the_series_from_search(self):
        self.assertEqual(drought.latest_series(SEARCH["results"]),
                         "/government/publications/dry-weather-and-drought-in-england-2026-summary-reports")

    def test_no_series(self):
        with self.assertRaises(ValueError):
            drought.latest_series(SEARCH["results"][1:])

    def test_the_latest_report_by_its_dates(self):
        series = json.loads(json.dumps(SERIES))
        series["details"]["attachments"].reverse()
        self.assertTrue(drought.latest_report(series)["title"].endswith("23 to 29 October 2026"))

    def test_report_dates(self):
        self.assertEqual(drought.report_date("Dry weather and drought in England: 25 September to 1 October 2026"),
                         date(2026, 10, 1))
        self.assertIsNone(drought.report_date("Something else"))


class Poll(unittest.TestCase):
    def answer(self, url, params=None):
        if url == drought.SEARCH_URL:
            return SEARCH
        if url.endswith("summary-reports"):
            return SERIES
        return REPORT

    def test_writes_the_summary(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(drought, "SUMMARY", Path(d) / "drought.json"), \
                mock.patch.object(drought, "get", side_effect=self.answer):
            drought.poll(now=1_793_600_000)
            s = json.loads((Path(d) / "drought.json").read_text())
        self.assertEqual(s["generated"], 1_793_600_000)
        self.assertEqual(s["report"]["period"], "23 to 29 October 2026")
        self.assertTrue(s["report"]["url"].startswith("https://www.gov.uk/government/publications/"))
        self.assertEqual(len(s["stages"]), 2)
        self.assertEqual(len(s["bans"]), 1)

    def test_a_failure_leaves_the_last_summary(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "drought.json"
            path.write_text('{"stages":[]}')
            with mock.patch.object(drought, "SUMMARY", path), \
                    mock.patch.object(drought, "get", side_effect=TimeoutError("timed out")):
                with self.assertRaises(TimeoutError):
                    drought.poll()
            self.assertEqual(path.read_text(), '{"stages":[]}')


if __name__ == "__main__":
    unittest.main()
