"""Observations at sea and on the coast.

Two sources, stored the same way:

The Met Office's moored stations in Cornish waters report each hour over the
World Meteorological Organization's network, and the US National Data Buoy
Center republishes the last 45 days of them as plain text, one file per
station. Seven Stones measures waves; E1 measures wind.

The Regional Coastal Monitoring Programme's wave buoys and coastal weather
stations report every half hour, through the Channel Coastal Observatory's API.
It needs a key, issued for this site's domain and sent with it as the Referer;
the key comes from the CCO_API_KEY environment variable, and without one these
stations are skipped. A site with both a buoy and a weather station, such as
Looe Bay, is one station here, placed at the buoy. The data are real time and
not quality-controlled.
"""

import calendar
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import net, overflows
from .ea import USER_AGENT
from .geo import in_region

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "site" / "data" / "marine.json"

REFRESH = 30 * 60           # the coastal stations report every half hour
STALE_AFTER = 6 * 3600
LATEST_WITHIN = 3 * 3600    # a reading counts as current within this of the newest
MAX_DAYS = 28

FIELDS = ("wdir", "wspd", "gst", "wvht", "dpd", "apd", "mwd", "pres", "atmp", "wtmp")

# ---------------------------------------------------------------- NDBC

NDBC_FEED = "https://www.ndbc.noaa.gov/data/realtime2/{}.txt"
# Positions from the NDBC's station list (activestations.xml).
NDBC = {
    "62107": {"name": "Seven Stones lightvessel", "lat": 50.102, "lon": -6.100},
    "62050": {"name": "E1 buoy", "lat": 50.0, "lon": -4.4},
}
NDBC_NAMES = {"WDIR": "wdir", "WSPD": "wspd", "GST": "gst", "WVHT": "wvht", "DPD": "dpd",
              "APD": "apd", "MWD": "mwd", "PRES": "pres", "ATMP": "atmp", "WTMP": "wtmp"}


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
            row[key] = _number(cells.get(name, "MM"))
        rows.append(row)
    return rows


def fetch(station):
    req = urllib.request.Request(NDBC_FEED.format(station), headers={"User-Agent": USER_AGENT})
    return parse(net.fetch(req).decode("ascii", "replace"))


# ---------------------------------------------------------------- CCO

CCO_FEED = "https://coastalmonitoring.org/observations/{}/{}.geojson"
CCO_REFERER = "https://cornwall.watch/"
CCO_FIELDS = {
    "waves": {"hs": "wvht", "tp": "dpd", "tz": "apd", "pdir": "mwd", "sst": "wtmp"},
    "met": {"speed": "wspd", "gust": "gst", "dir": "wdir", "temp": "atmp", "pressure": "pres"},
}
CCO_SILENT = 30 * 86400     # a sensor silent this long is left off the map
CCO_HISTORY_HOURS = 168     # a week, the most the API gives for one sensor in one go


def cco_time(text):
    """'20261030#133000' (UTC) to Unix seconds, or None."""
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})#(\d{2})(\d{2})(\d{2})", str(text or ""))
    return calendar.timegm(tuple(int(g) for g in m.groups())) if m else None


def cco_id(name):
    """'St Mary's Sound' -> 'cco-st-mary-s-sound'."""
    return ("cco-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))[:24]


def cco_row(kind, props):
    ts = cco_time(props.get("date"))
    if ts is None:
        return None
    row = {"ts": ts}
    for name, key in CCO_FIELDS[kind].items():
        row[key] = _number(props.get(name))
    return row


def cco_get(kind, key, params=None, when="latest"):
    """A CCO feed: the latest readings, or with `when` (YYYYMMDDHHMMSS) and a
    duration in hours, those up to that time, which for tide predictions can
    be in the future."""
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(CCO_FEED.format(kind, when) + query, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json",
        "X-API-Key": key, "Referer": os.environ.get("CCO_REFERER", CCO_REFERER)})
    return json.loads(net.fetch(req))


def cco_sites(latest, belongs, now):
    """The Cornish sites in the latest observations, keyed by sensor name:
    {name: {"lat", "lon", "kinds": {kind: (sensor id, latest reading)}}}."""
    sites = {}
    for kind, fc in latest.items():
        for f in fc.get("features", []):
            p = f.get("properties") or {}
            name, ts = p.get("sensor"), cco_time(p.get("date"))
            try:
                lon, lat = f["geometry"]["coordinates"][:2]
            except (KeyError, TypeError, ValueError):
                continue
            if not name or ts is None or now - ts > CCO_SILENT or not in_region(lat, lon) or not belongs(lon, lat):
                continue
            site = sites.setdefault(name, {"kinds": {}})
            if kind == "waves" or "lat" not in site:   # the buoy's position where there is one
                site.update(lat=round(lat, 5), lon=round(lon, 5))
            site["kinds"][kind] = (str(p.get("id")), cco_row(kind, p))
    return sites


def poll_cco(con, key, now=None):
    """Read the coastal stations: their latest readings, and any history
    missing since the last poll, up to a week. Returns readings stored."""
    now = int(now or time.time())
    latest = {kind: cco_get(kind, key) for kind in CCO_FIELDS}
    sites = cco_sites(latest, overflows.membership(), now)
    n = 0
    for name, site in sites.items():
        sid = cco_id(name)
        upsert_station(con, sid, name, "cco", site["lat"], site["lon"], now)
        for kind, (sensor, row) in site["kinds"].items():
            field = next(iter(CCO_FIELDS[kind].values()))
            held = con.execute(f"SELECT MAX(ts) FROM marine_obs WHERE station = ? AND {field} IS NOT NULL",
                               (sid,)).fetchone()[0]
            # The latest reading first, so it shows even if the history fails,
            # then whatever came between it and the last one held.
            store(con, sid, [row])
            n += 1
            if held is not None and row["ts"] - held <= 3600:
                continue
            hours = CCO_HISTORY_HOURS if held is None else min(CCO_HISTORY_HOURS, math.ceil((row["ts"] - held) / 3600) + 1)
            fc = cco_get(kind, key, {"sensor": sensor, "duration": hours})
            rows = [r for r in (cco_row(kind, f.get("properties") or {}) for f in fc.get("features", [])) if r]
            store(con, sid, rows)
            n += len(rows)
    ids = [cco_id(name) for name in sites]
    con.execute(f"UPDATE marine_stations SET active = 0 WHERE source = 'cco' AND id NOT IN "
                f"({','.join('?' * len(ids)) or 'NULL'})", ids)
    return n


# ---------------------------------------------------------------- tides

TIDE_AHEAD = 48         # hours of predictions fetched at a time
TIDE_KEEP_AHEAD = 36    # fetch again once fewer hours than this are held
TIDE_EVENTS_SHOWN = 4


def tide_events(features):
    """The high and low waters named in a run of predictions: each one carries
    the next high and low after it. Returns [(ts, 'high'|'low', height)]."""
    events = {}
    for f in features:
        p = f.get("properties") or {}
        for kind, height, when in (("high", p.get("hw"), p.get("hw_timestamp")),
                                   ("low", p.get("lw"), p.get("lw_timestamp"))):
            ts = cco_time(when)
            if ts is not None:
                events[ts] = (ts, kind, _number(height))
    return sorted(events.values())


def poll_tides(con, key, now=None):
    """Read tide predictions for the Cornish sites that publish them, when
    fewer than TIDE_KEEP_AHEAD hours of them are held. Returns events stored."""
    now = int(now or time.time())
    belongs = overflows.membership()
    n = 0
    for f in cco_get("tidepredictions", key).get("features", []):
        p = f.get("properties") or {}
        name = p.get("sensor")
        try:
            lon, lat = f["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            continue
        # Sites listed without a reading publish their predictions elsewhere.
        if not name or cco_time(p.get("date")) is None or not in_region(lat, lon) or not belongs(lon, lat):
            continue
        sid = cco_id(name)
        held = con.execute("SELECT MAX(ts) FROM tide_events WHERE station = ?", (sid,)).fetchone()[0]
        if held and held - now > TIDE_KEEP_AHEAD * 3600:
            continue
        if not con.execute("SELECT 1 FROM marine_stations WHERE id = ?", (sid,)).fetchone():
            upsert_station(con, sid, name, "cco", round(lat, 5), round(lon, 5), now)
        when = time.strftime("%Y%m%d%H%M%S", time.gmtime(now + TIDE_AHEAD * 3600))
        fc = cco_get("tidepredictions", key, {"sensor": str(p.get("id")), "duration": TIDE_AHEAD}, when=when)
        events = tide_events(fc.get("features", []))
        con.executemany("INSERT OR REPLACE INTO tide_events (station, ts, kind, height) VALUES (?, ?, ?, ?)",
                        [(sid, ts, kind, h) for ts, kind, h in events])
        n += len(events)
    con.execute("DELETE FROM tide_events WHERE ts < ?", (now - 86400,))
    return n


# ---------------------------------------------------------------- storage

def _number(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def upsert_station(con, sid, name, source, lat, lon, now):
    con.execute("""INSERT INTO marine_stations (id, name, source, lat, lon, active, updated)
                   VALUES (?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(id) DO UPDATE SET name = excluded.name, lat = excluded.lat,
                     lon = excluded.lon, active = 1, updated = excluded.updated""",
                (sid, name, source, lat, lon, now))


def store(con, station, rows):
    """Store readings, keeping values already held where a row lacks them: a
    coastal site's buoy and weather station report the same times separately."""
    con.executemany(
        f"INSERT INTO marine_obs (station, ts, {', '.join(FIELDS)}) "
        f"VALUES (?, ?, {', '.join('?' * len(FIELDS))}) "
        f"ON CONFLICT(station, ts) DO UPDATE SET "
        + ", ".join(f"{f} = COALESCE(excluded.{f}, {f})" for f in FIELDS),
        [[station, r["ts"]] + [r.get(f) for f in FIELDS] for r in rows])


def poll(con, now=None):
    """Read every source. A failure at one station or source doesn't stop the
    others. Returns (readings stored, [errors])."""
    now = int(now or time.time())
    n, errors = 0, []
    for sid, s in NDBC.items():
        try:
            rows = fetch(sid)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{sid}: {e}")
            continue
        upsert_station(con, sid, s["name"], "ndbc", s["lat"], s["lon"], now)
        store(con, sid, rows)
        n += len(rows)
    key = os.environ.get("CCO_API_KEY")
    if key:
        try:
            n += poll_cco(con, key, now)
        except Exception as e:  # noqa: BLE001
            errors.append(f"coastal stations: {e}")
        try:
            poll_tides(con, key, now)
        except Exception as e:  # noqa: BLE001
            errors.append(f"tide predictions: {e}")
    con.commit()
    return n, errors


# ---------------------------------------------------------------- output

def latest(con, station):
    """The newest value of each field, if it's within LATEST_WITHIN of the newest reading."""
    newest = con.execute("SELECT MAX(ts) FROM marine_obs WHERE station = ?", (station,)).fetchone()[0]
    if newest is None:
        return None, {}
    values = {}
    for f in FIELDS:
        r = con.execute(f"SELECT {f} FROM marine_obs WHERE station = ? AND {f} IS NOT NULL "
                        f"AND ts >= ? ORDER BY ts DESC LIMIT 1", (station, newest - LATEST_WITHIN)).fetchone()
        if r:
            values[f] = r[0]
    return newest, values


def summary(con, now=None):
    now = int(now or time.time())
    out = []
    for s in con.execute("SELECT * FROM marine_stations WHERE active = 1 ORDER BY source DESC, name"):
        if not in_region(s["lat"], s["lon"]):
            continue
        ts, values = latest(con, s["id"])
        entry = {"id": s["id"], "name": s["name"], "source": s["source"], "lat": s["lat"], "lon": s["lon"]}
        tides = con.execute("SELECT ts, kind, height FROM tide_events WHERE station = ? AND ts > ? ORDER BY ts LIMIT ?",
                            (s["id"], now, TIDE_EVENTS_SHOWN)).fetchall()
        if tides:
            entry["tides"] = [list(t) for t in tides]
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
    """Readings for a station's popup chart: {"fields": [...], "readings": [[ts, ...], ...]},
    or None for an unknown station."""
    s = con.execute("SELECT name FROM marine_stations WHERE id = ?", (station,)).fetchone()
    if s is None:
        return None
    now = int(now or time.time())
    days = max(1, min(MAX_DAYS, days))
    rows = con.execute(f"SELECT ts, {', '.join(FIELDS)} FROM marine_obs WHERE station = ? AND ts > ? ORDER BY ts",
                       (station, now - days * 86400)).fetchall()
    return {"id": station, "name": s["name"], "days": days,
            "fields": list(FIELDS), "readings": [list(r) for r in rows]}
