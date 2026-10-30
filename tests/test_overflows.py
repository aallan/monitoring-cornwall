"""Storm overflows: the spill log built by polling, states, and the summaries."""

import unittest
from unittest import mock

from monitor import overflows
from tests.support import NOW, DBTestCase

H = 3600


class SpillLog(DBTestCase):
    def events(self, oid="A"):
        return [tuple(r) for r in self.con.execute(
            "SELECT started, ended FROM overflow_events WHERE id = ? ORDER BY started", (oid,))]

    def test_a_spill_opens_then_closes(self):
        overflows._record_event(self.con, "A", 1, NOW, None)
        self.assertEqual(self.events(), [(NOW, None)])
        overflows._record_event(self.con, "A", 1, NOW, None)       # still spilling: no change
        self.assertEqual(self.events(), [(NOW, None)])
        overflows._record_event(self.con, "A", 0, NOW, NOW + 2 * H)
        self.assertEqual(self.events(), [(NOW, NOW + 2 * H)])

    def test_a_spill_wholly_between_polls_is_caught(self):
        overflows._record_event(self.con, "A", 0, NOW, NOW + H)
        self.assertEqual(self.events(), [(NOW, NOW + H)])

    def test_a_new_spill_closes_an_open_one(self):
        overflows._record_event(self.con, "A", 1, NOW, None)
        overflows._record_event(self.con, "A", 1, NOW + 5 * H, None)
        self.assertEqual(self.events(), [(NOW, NOW + 5 * H), (NOW + 5 * H, None)])

    def test_the_feed_going_backwards_is_ignored(self):
        overflows._record_event(self.con, "A", 0, NOW, NOW + H)
        overflows._record_event(self.con, "A", 0, NOW - 10 * H, NOW - 9 * H)
        self.assertEqual(self.events(), [(NOW, NOW + H)])

    def test_no_event_time(self):
        overflows._record_event(self.con, "A", 0, None, None)
        self.assertEqual(self.events(), [])

    def test_an_end_before_the_start_leaves_the_spill_open(self):
        overflows._record_event(self.con, "A", 0, NOW, NOW - H)
        self.assertEqual(self.events(), [(NOW, None)])

    def test_poll_reads_only_our_overflows(self):
        self.add_overflow("OURS")
        feed = [
            {"Id": "OURS", "status": 1, "statusStart": NOW * 1000, "lastUpdated": NOW * 1000,
             "latestEventStart": NOW * 1000, "latestEventEnd": None},
            {"Id": "DEVON", "status": 1, "latestEventStart": NOW * 1000},
        ]
        with mock.patch.object(overflows, "fetch_live", return_value=feed):
            self.assertEqual(overflows.poll_overflows(self.con), 1)
        self.assertEqual(self.events("OURS"), [(NOW, None)])
        self.assertEqual(self.events("DEVON"), [])
        row = self.con.execute("SELECT status, status_start FROM overflows WHERE id = 'OURS'").fetchone()
        self.assertEqual(tuple(row), (1, NOW))


class States(unittest.TestCase):
    def test_states(self):
        row = lambda status, last_end: {"status": status, "last_end": last_end}
        self.assertEqual(overflows.state(row(-1, None), NOW), "offline")
        self.assertEqual(overflows.state(row(1, NOW), NOW), "spilling")
        self.assertEqual(overflows.state(row(0, NOW - 47 * H), NOW), "recent")
        self.assertEqual(overflows.state(row(0, NOW - 49 * H), NOW), "quiet")
        self.assertEqual(overflows.state(row(0, None), NOW), "quiet")

    def test_at_works(self):
        self.assertTrue(overflows.at_works("Inlet SO at WwTW"))
        self.assertTrue(overflows.at_works("Storm tank at WwTW - with treatment"))
        self.assertFalse(overflows.at_works("SO on sewer network"))
        self.assertFalse(overflows.at_works("Storm discharge at pumping station"))
        self.assertFalse(overflows.at_works(None))

    def test_tidy_names(self):
        self.assertEqual(overflows.tidy_site_name("ALLOTMENT CSO"), "Allotment CSO")
        self.assertEqual(overflows.tidy_site_name("LAND OF THE PS"), "Land of the PS")
        self.assertEqual(overflows.tidy_water("CAMEL ESTUARY (E)"), "Camel Estuary")
        self.assertEqual(overflows.tidy_water("RIVER OF THE NORTH"), "River of the North")
        self.assertIsNone(overflows.tidy_water(None))


class Summaries(DBTestCase):
    def test_summary(self):
        self.add_overflow("WORKS", status=0, asset_type="Inlet SO at WwTW")
        self.add_overflow("NOW", status=1, status_start=NOW - H)
        self.con.executemany("INSERT INTO overflow_events (id, started, ended) VALUES (?, ?, ?)",
                             [("WORKS", NOW - 10 * H, NOW - 5 * H), ("NOW", NOW - H, None)])
        self.con.commit()
        out = {o["id"]: o for o in overflows.summary(self.con, NOW)["overflows"]}
        self.assertEqual(out["WORKS"]["state"], "recent")
        self.assertTrue(out["WORKS"]["works"])
        self.assertEqual(out["WORKS"]["last_end"], NOW - 5 * H)
        self.assertEqual(out["NOW"]["state"], "spilling")
        self.assertNotIn("works", out["NOW"])
        self.assertNotIn("last_end", out["NOW"])

    def test_an_overflow_never_polled_is_left_out(self):
        self.add_overflow("NEW", status=None)
        self.assertEqual(overflows.summary(self.con, NOW)["overflows"], [])

    def test_detail_shows_the_last_30_days(self):
        self.add_overflow("A")
        self.con.executemany("INSERT INTO overflow_events (id, started, ended) VALUES (?, ?, ?)",
                             [("A", NOW - 40 * 86400, NOW - 39 * 86400),   # too old
                              ("A", NOW - 31 * 86400, NOW - 29 * 86400),   # overlaps the window
                              ("A", NOW - H, None)])
        self.con.execute("INSERT INTO overflow_annual (id, year, hours, spills) VALUES ('A', 2025, 123.456, 9)")
        self.con.commit()
        d = overflows.detail(self.con, "A", NOW)
        self.assertEqual(d["events"], [[NOW - 31 * 86400, NOW - 29 * 86400], [NOW - H, None]])
        self.assertEqual(d["annual"], [[2025, 123.5, 9]])
        self.assertIsNone(overflows.detail(self.con, "MISSING", NOW))


if __name__ == "__main__":
    unittest.main()
