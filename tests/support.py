"""Shared test helpers: a fresh database per test, and canned feed data."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from monitor import cache, db, ea, floods, overflows

NOW = 1_793_361_600  # Fri 30 Oct 2026 12:00 UTC


class QuietTestCase(unittest.TestCase):
    """Silences the monitor's log lines, which tests of failures provoke."""

    def setUp(self):
        for module in (cache, ea, floods, overflows):
            patcher = mock.patch.object(module, "log")
            patcher.start()
            self.addCleanup(patcher.stop)


class DBTestCase(QuietTestCase):
    """Points the monitor's database at a temporary file for each test."""

    def setUp(self):
        super().setUp()
        self._dir = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(db, "DB_PATH", Path(self._dir.name) / "monitor.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        self.con = db.connect()
        self.addCleanup(self.con.close)
        cache.clear()
        self.addCleanup(cache.clear)

    def add_station(self, sid, kind="level", measure=None, name="Somewhere", lat=50.4, lon=-4.9,
                    unit="m", typical=(0.2, 1.0), record=2.0, river="River Test"):
        measure = measure or f"{sid}-level-stage-i-15_min-m"
        self.con.execute(
            """INSERT INTO stations (id, kind, measure, name, river, lat, lon, unit,
                                     typical_low, typical_high, max_on_record, updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, kind, measure, name, river, lat, lon, unit,
             typical[0] if typical else None, typical[1] if typical else None, record, NOW))
        self.con.commit()
        return measure

    def add_readings(self, measure, rows):
        self.con.executemany("INSERT INTO readings (measure, ts, value) VALUES (?, ?, ?)",
                             [(measure, ts, v) for ts, v in rows])
        self.con.commit()

    def add_overflow(self, oid, status=0, asset_type="SO on sewer network", status_start=None,
                     name="Test CSO", water="River Test", lat=50.4, lon=-4.9):
        self.con.execute(
            """INSERT INTO overflows (id, name, asset_type, water, lat, lon, status, status_start, updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (oid, name, asset_type, water, lat, lon, status, status_start, NOW))
        self.con.commit()


def measure_item(notation, qualifier="Stage", unit="m", parameter="level", period=900):
    """A measure as the EA station list describes it."""
    return {"@id": f"http://environment.data.gov.uk/flood-monitoring/id/measures/{notation}",
            "parameter": parameter, "qualifier": qualifier, "unitName": unit, "period": period}
