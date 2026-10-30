"""Storm overflows: South West Water's Event Duration Monitor (EDM) feed.

The live feed is South West Water's contribution to Water UK's National Storm
Overflow Hub, an open ArcGIS FeatureServer. For each overflow it reports the
current state and only the most recent spill, so the spill history here is
built up by polling and noting each new event.

Names, asset types and yearly totals come from the regulatory annual returns,
published as a second FeatureServer.
"""

import gzip
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import db
from .ea import USER_AGENT, log
from .geo import LandMask

LIVE_URL = ("https://services-eu1.arcgis.com/OMdMOtfhATJPcHe3/arcgis/rest/services/"
            "NEH_outlets_PROD/FeatureServer/0/query")
ANNUAL_URL = ("https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services/"
              "edm_annual_returns_all_years_public/FeatureServer/0/query")

ROOT = Path(__file__).resolve().parent.parent
BOUNDARY = ROOT / "site" / "data" / "boundary.geojson"
NEIGHBOURS = ROOT / "data" / "neighbours.geojson"
SUMMARY = ROOT / "site" / "data" / "overflows.json"

SPILLING, QUIET, OFFLINE = 1, 0, -1
# Outfalls can sit offshore, beyond the low-water boundary. One within this
# distance of Cornwall, and nearer to Cornwall than to Devon or Plymouth,
# counts as Cornish.
OFFSHORE_M = 3000
RECENT = 48 * 3600          # 'spilled recently' window, as on Floodwatch
HISTORY_WINDOW = 30 * 86400

# Small words that stay lower case inside a name.
SMALL_WORDS = {"of", "the", "and", "to", "at", "via", "on", "in", "by"}
# Words in site names that stay in capitals.
ACRONYMS = {"CSO", "SO", "SSO", "EO", "STW", "STWS", "WTW", "WWTW", "SPS", "PS", "SWS", "TPS", "WPS", "UV"}


def post(url, params, retries=3):
    """POST a FeatureServer query (long WHERE clauses don't fit in a GET)."""
    body = urllib.parse.urlencode({**params, "f": "json"}).encode()
    delay = 2
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers={
            "User-Agent": USER_AGENT, "Accept-Encoding": "gzip",
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            data = json.loads(raw)
            if "error" in data:
                raise RuntimeError(data["error"])
            return data
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as e:
            if attempt == retries - 1:
                raise
            log(f"overflow request failed ({e}); retrying in {delay}s")
            time.sleep(delay)
            delay *= 2


def seconds(ms):
    return int(ms / 1000) if isinstance(ms, (int, float)) else None


def tidy_site_name(raw):
    """'ALLOTMENT CSO' -> 'Allotment CSO'."""
    words = []
    parts = str(raw).split()
    for i, w in enumerate(parts):
        bare = w.strip("(),").upper()
        if bare in ACRONYMS:
            words.append(w.upper())
        elif 0 < i < len(parts) - 1 and w.lower() in SMALL_WORDS:
            words.append(w.lower())
        else:
            words.append(w.capitalize())
    return " ".join(words)


def tidy_water(raw):
    """'CAMEL ESTUARY (E)' -> 'Camel Estuary'. The bracketed suffixes mark the
    water's type: (E) estuarine, (C) coastal."""
    text = str(raw or "").split("(")[0].strip()
    words = [w.lower() if i and w.lower() in SMALL_WORDS else w.capitalize() for i, w in enumerate(text.split())]
    return " ".join(words) or None


# ---------------------------------------------------------------- which overflows

def _rings(path):
    fc = json.loads(Path(path).read_text())
    return [[tuple(p) for p in poly[0]] for f in fc["features"] for poly in f["geometry"]["coordinates"]]


def membership():
    """A test for whether an overflow belongs to Cornwall."""
    cornwall = LandMask(_rings(BOUNDARY), 0)
    neighbours = LandMask(_rings(NEIGHBOURS), 0) if NEIGHBOURS.exists() else None

    def belongs(lon, lat):
        p = (lon, lat)
        if cornwall.inside(p):
            return True
        if neighbours and neighbours.inside(p):
            return False
        near = cornwall.distance_m(p, OFFSHORE_M)
        if near == math.inf:
            return False
        return neighbours is None or near < neighbours.distance_m(p, OFFSHORE_M)
    return belongs


def fetch_live():
    data = post(LIVE_URL, {
        "where": "1=1",
        "outFields": "Id,receivingWaterCourse,status,statusStart,latestEventStart,latestEventEnd,lastUpdated,latitude,longitude",
        "returnGeometry": "false", "resultRecordCount": 4000})
    return [f["attributes"] for f in data.get("features", [])]


def fetch_annual():
    """Every South West Water row in the annual returns, a page at a time."""
    rows, offset = [], 0
    while True:
        data = post(ANNUAL_URL, {
            "where": "water_company_name='South West Water'",
            "outFields": "annual_return_year,unique_id,wasc_supplementary_permit_ref_opt,site_name_ea_condat,"
                         "storm_discharge_asset_type,total_spill_duration_hrs_calculated,"
                         "counted_spills_12_24hr_calculated",
            "returnGeometry": "false", "orderByFields": "ObjectId",
            "resultRecordCount": 1000, "resultOffset": offset})
        page = [f["attributes"] for f in data.get("features", [])]
        rows += page
        if not data.get("exceededTransferLimit") or not page:
            return rows
        offset += len(page)
        time.sleep(0.3)


def refresh_overflows(con):
    """Rebuild the list of overflows, their names and yearly totals. Daily."""
    belongs = membership()
    now = int(time.time())
    seen = []
    for s in fetch_live():
        lat, lon = s.get("latitude"), s.get("longitude")
        if not s.get("Id") or not isinstance(lat, (int, float)) or not belongs(lon, lat):
            continue
        con.execute(
            """INSERT INTO overflows (id, water, lat, lon, active, updated) VALUES (?, ?, ?, ?, 1, ?)
               ON CONFLICT(id) DO UPDATE SET water=excluded.water, lat=excluded.lat,
                 lon=excluded.lon, active=1, updated=excluded.updated""",
            (s["Id"], tidy_water(s.get("receivingWaterCourse")), round(lat, 5), round(lon, 5), now))
        seen.append(s["Id"])
    if seen:
        con.execute(f"UPDATE overflows SET active = 0 WHERE id NOT IN ({','.join('?' * len(seen))})", seen)

    # Annual returns: rows from 2024 on carry the live feed's permit ID; older
    # rows are linked to it through the company's own permit reference.
    rows = fetch_annual()
    to_id = {r["wasc_supplementary_permit_ref_opt"]: r["unique_id"]
             for r in rows if r.get("unique_id") and r.get("wasc_supplementary_permit_ref_opt")}
    ours = set(seen)
    latest_year = {}
    for r in rows:
        oid = r.get("unique_id") or to_id.get(r.get("wasc_supplementary_permit_ref_opt"))
        if oid not in ours:
            continue
        try:
            year = int(r["annual_return_year"])
        except (TypeError, ValueError):
            continue
        hours, spills = r.get("total_spill_duration_hrs_calculated"), r.get("counted_spills_12_24hr_calculated")
        con.execute("INSERT OR REPLACE INTO overflow_annual (id, year, hours, spills) VALUES (?, ?, ?, ?)",
                    (oid, year, hours if isinstance(hours, (int, float)) else None,
                     int(spills) if isinstance(spills, (int, float)) else None))
        if year >= latest_year.get(oid, 0):
            latest_year[oid] = year
            con.execute("UPDATE overflows SET name = COALESCE(?, name), asset_type = COALESCE(?, asset_type) WHERE id = ?",
                        (tidy_site_name(r["site_name_ea_condat"]) if r.get("site_name_ea_condat") else None,
                         r.get("storm_discharge_asset_type"), oid))
    con.commit()
    return len(seen)


# ---------------------------------------------------------------- polling

def _record_event(con, oid, status, start, end):
    """Apply the feed's latest event for one overflow to its spill log.

    A new start time means a new spill; if the previous spill was still open,
    it ended at some point we didn't see, recorded as the new spill's start.
    The same start time with the overflow now quiet closes the open spill.
    """
    if start is None:
        return
    tail = con.execute("SELECT started, ended FROM overflow_events WHERE id = ? ORDER BY started DESC LIMIT 1",
                       (oid,)).fetchone()
    finished = end if status != SPILLING and end is not None and end >= start else None
    if tail and tail["started"] == start:
        if tail["ended"] is None and finished is not None:
            con.execute("UPDATE overflow_events SET ended = ? WHERE id = ? AND started = ?", (finished, oid, start))
        return
    if tail and start < tail["started"]:
        return  # the feed went backwards; keep what we have
    if tail and tail["ended"] is None:
        con.execute("UPDATE overflow_events SET ended = ? WHERE id = ? AND started = ?", (start, oid, tail["started"]))
    con.execute("INSERT OR IGNORE INTO overflow_events (id, started, ended) VALUES (?, ?, ?)", (oid, start, finished))


def poll_overflows(con):
    ours = {r["id"] for r in con.execute("SELECT id FROM overflows WHERE active = 1")}
    n = 0
    for s in fetch_live():
        oid = s.get("Id")
        if oid not in ours:
            continue
        status = s.get("status")
        con.execute("UPDATE overflows SET status = ?, status_start = ?, last_seen = ? WHERE id = ?",
                    (status, seconds(s.get("statusStart")), seconds(s.get("lastUpdated")), oid))
        _record_event(con, oid, status, seconds(s.get("latestEventStart")), seconds(s.get("latestEventEnd")))
        n += 1
    con.commit()
    return n


# ---------------------------------------------------------------- the map's summary

def state(row, now):
    if row["status"] == OFFLINE:
        return "offline"
    if row["status"] == SPILLING:
        return "spilling"
    last_end = row["last_end"]
    if last_end is not None and now - last_end <= RECENT:
        return "recent"
    return "quiet"


def summary(con, now=None):
    now = int(now or time.time())
    rows = con.execute("""
        SELECT o.*, (SELECT MAX(COALESCE(e.ended, ?)) FROM overflow_events e WHERE e.id = o.id) AS last_end
        FROM overflows o WHERE o.active = 1 AND o.status IS NOT NULL ORDER BY o.id""", (now,)).fetchall()
    out = []
    for r in rows:
        entry = {"id": r["id"], "lat": r["lat"], "lon": r["lon"], "state": state(r, now)}
        if r["name"]:
            entry["name"] = r["name"]
        if r["water"]:
            entry["water"] = r["water"]
        if r["status_start"]:
            entry["since"] = r["status_start"]
        if r["last_end"] is not None and r["status"] != SPILLING:
            entry["last_end"] = r["last_end"]
        out.append(entry)
    return {"generated": now, "overflows": out}


def write_summary(con, now=None):
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary(con, now), separators=(",", ":"), ensure_ascii=False))
    tmp.replace(SUMMARY)


def detail(con, oid, now=None):
    """Everything an overflow's popup shows."""
    now = int(now or time.time())
    o = con.execute("SELECT * FROM overflows WHERE id = ?", (oid,)).fetchone()
    if o is None:
        return None
    since = now - HISTORY_WINDOW
    events = [[e["started"], e["ended"]] for e in con.execute(
        "SELECT started, ended FROM overflow_events WHERE id = ? AND COALESCE(ended, ?) > ? ORDER BY started",
        (oid, now, since))]
    annual = [[a["year"], round(a["hours"], 1) if a["hours"] is not None else None, a["spills"]] for a in con.execute(
        "SELECT year, hours, spills FROM overflow_annual WHERE id = ? ORDER BY year", (oid,))]
    return {
        "id": o["id"], "name": o["name"], "asset_type": o["asset_type"], "water": o["water"],
        "status": o["status"], "status_start": o["status_start"], "last_seen": o["last_seen"],
        "events": events, "annual": annual,
        "log_started": int(db.get_meta(con, "overflow_log_started", 0)) or None,
    }
