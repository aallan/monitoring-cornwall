"""Offshore weather and sea observations from Met Office stations in Cornish
waters, as relayed by the US National Data Buoy Center.

The Met Office's moored stations report each hour over the World
Meteorological Organization's network, and the NDBC republishes the last 45
days of them as plain text, one file per station. Each station reports what
its instruments measure: Seven Stones has wave height and period but often no
wind; E1 has wind but no waves.
"""

import calendar
import json
import time
import urllib.request
from pathlib import Path

from . import net
from .ea import USER_AGENT
from .geo import in_region

FEED = "https://www.ndbc.noaa.gov/data/realtime2/{}.txt"

# Positions from the NDBC's station list (activestations.xml).
STATIONS = {
    "62107": {"name": "Seven Stones lightvessel", "lat": 50.102, "lon": -6.100},
    "62050": {"name": "E1 buoy", "lat": 50.0, "lon": -4.4},
}

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "site" / "data" / "marine.json"

REFRESH = 30 * 60           # stations report hourly
STALE_AFTER = 6 * 3600
LATEST_WITHIN = 3 * 3600    # a reading counts as current within this of the newest
MAX_DAYS = 28

# Columns in the NDBC's standard meteorological format, and the ones kept.
FIELDS = ("wdir", "wspd", "gst", "wvht", "dpd", "apd", "pres", "atmp", "wtmp")
NDBC_NAMES = {"WDIR": "wdir", "WSPD": "wspd", "GST": "gst", "WVHT": "wvht", "DPD": "dpd",
              "APD": "apd", "PRES": "pres", "ATMP": "atmp", "WTMP": "wtmp"}


def parse(text):
    """Rows of {ts, field: value} from an NDBC realtime2 file; 'MM' is missing."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("#"):
        raise ValueError("not an NDBC observation file")
    header = lines[0].lstrip("#").split()
    rows = []
    for line in lines:
        if line.startswith("#") or not line.strip():
            continue
        cells = dict(zip(header, line.split()))
        try:
            ts = calendar.timegm((int(cells["YY"]), int(cells["MM"]), int(cells["DD"]),
                                  int(cells["hh"]), int(cells["mm"]), 0))
        except (KeyError, ValueError):
            continue
        row = {"ts": ts}
        for name, key in NDBC_NAMES.items():
            v = cells.get(name, "MM")
            try:
                row[key] = None if v == "MM" else float(v)
            except ValueError:
                row[key] = None
        rows.append(row)
    return rows


def fetch(station):
    req = urllib.request.Request(FEED.format(station), headers={"User-Agent": USER_AGENT})
    return parse(net.fetch(req).decode("ascii", "replace"))


def store(con, station, rows):
    con.executemany(
        f"INSERT OR REPLACE INTO marine_obs (station, ts, {', '.join(FIELDS)}) "
        f"VALUES (?, ?, {', '.join('?' * len(FIELDS))})",
        [[station, r["ts"]] + [r[f] for f in FIELDS] for r in rows])


def poll(con):
    """Read every station's feed. A failure at one station doesn't stop the others."""
    n = 0
    errors = []
    for station in STATIONS:
        try:
            rows = fetch(station)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{station}: {e}")
            continue
        store(con, station, rows)
        n += len(rows)
    con.commit()
    return n, errors


def latest(con, station, now):
    """The newest value of each field, if it's within LATEST_WITHIN of the newest reading."""
    newest = con.execute("SELECT MAX(ts) FROM marine_obs WHERE station = ?", (station,)).fetchone()[0]
    if newest is None:
        return None, {}
    values = {}
    for f in FIELDS:
        r = con.execute(f"SELECT ts, {f} FROM marine_obs WHERE station = ? AND {f} IS NOT NULL "
                        f"AND ts >= ? ORDER BY ts DESC LIMIT 1", (station, newest - LATEST_WITHIN)).fetchone()
        if r:
            values[f] = r[1]
    return newest, values


def summary(con, now=None):
    now = int(now or time.time())
    out = []
    for sid, s in STATIONS.items():
        if not in_region(s["lat"], s["lon"]):
            continue
        ts, values = latest(con, sid, now)
        entry = {"id": sid, "name": s["name"], "lat": s["lat"], "lon": s["lon"]}
        if ts is None:
            entry["state"] = "no-data"
        else:
            entry.update(ts=ts, state="stale" if now - ts > STALE_AFTER else "live", **values)
        out.append(entry)
    return {"generated": now, "stations": out}


def write_summary(con, now=None):
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary(con, now), separators=(",", ":")))
    tmp.replace(SUMMARY)


def history(con, station, days, now=None):
    """Readings for a station's popup chart: {"fields": [...], "readings": [[ts, ...], ...]}."""
    now = int(now or time.time())
    days = max(1, min(MAX_DAYS, days))
    rows = con.execute(f"SELECT ts, {', '.join(FIELDS)} FROM marine_obs WHERE station = ? AND ts > ? ORDER BY ts",
                       (station, now - days * 86400)).fetchall()
    return {"id": station, "name": STATIONS[station]["name"], "days": days,
            "fields": list(FIELDS), "readings": [list(r) for r in rows]}
