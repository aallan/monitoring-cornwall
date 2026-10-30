"""Client for the Environment Agency real-time flood-monitoring API.

The API is open, with no key, under the Open Government Licence:
https://environment.data.gov.uk/flood-monitoring/doc/reference
"""

import gzip
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .geo import LandMask, in_region

API = "https://environment.data.gov.uk/flood-monitoring"
USER_AGENT = "monitoring-cornwall/0.1 (+https://github.com/aallan/monitoring-cornwall)"

ROOT = Path(__file__).resolve().parent.parent
BOUNDARY = ROOT / "site" / "data" / "boundary.geojson"
PLACES = ROOT / "data" / "places.json"

# Station search: a circle that covers the county, then the county boundary.
CENTRE = (50.40, -4.95)
RADIUS_KM = 110
# Gauges on the Tamar sit on either bank of the border river.
BORDER_TOLERANCE_M = 1500

LEVEL_UNITS = ("m", "mASD", "mAOD")
# Tidal gauges report against Ordnance Datum where they can; prefer that series.
TIDAL_UNITS = ("mAOD", "m", "mASD")


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", file=sys.stderr, flush=True)


def get(path, params=None, retries=3):
    """GET a JSON document from the API, gzip-compressed on the wire."""
    url = API + path + ("?" + urllib.parse.urlencode(params, safe=",") if params else "")
    delay = 2
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return json.loads(body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == retries - 1:
                raise
            log(f"EA request failed ({e}); retrying in {delay}s: {path}")
            time.sleep(delay)
            delay *= 2


def to_ts(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def to_iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def number(v):
    """Readings occasionally arrive as a list of duplicates; take the first number."""
    if isinstance(v, list):
        v = next((x for x in v if isinstance(x, (int, float))), None)
    return float(v) if isinstance(v, (int, float)) else None


# ---------------------------------------------------------------- stations

def load_land():
    fc = json.loads(BOUNDARY.read_text())
    rings = [[tuple(p) for p in poly[0]] for f in fc["features"] for poly in f["geometry"]["coordinates"]]
    return LandMask(rings, BORDER_TOLERANCE_M)


def load_places():
    try:
        return json.loads(PLACES.read_text())
    except FileNotFoundError:
        return []


def tidy_name(label):
    """EA labels are often upper case: 'CANWORTHY WATER BRIDGE' -> 'Canworthy Water Bridge'."""
    label = " ".join(str(label).split())
    if label.isupper():
        label = " ".join(w.capitalize() if w.isalpha() else w.title() for w in label.split())
    return label


def nearest_place(lat, lon, places):
    """The nearest town or village, unless a hamlet is a good deal closer."""
    best, best_d = None, math.inf
    kx = 111.32 * math.cos(math.radians(lat))
    for p in places:
        d = math.hypot((p["lon"] - lon) * kx, (p["lat"] - lat) * 110.57)
        if p["place"] == "hamlet":
            d *= 2  # prefer names people recognise
        if d < best_d:
            best, best_d = p["name"], d
    return best


def measure_list(station):
    ms = station.get("measures", [])
    return ms if isinstance(ms, list) else [ms]


def pick_measure(station, parameter):
    """Choose the one series a station shows, and what kind of station it is.

    Returns (kind, notation, unit), or None if the station has nothing we show.
    """
    ms = measure_list(station)

    def find(qualifier, units):
        for unit in units:
            for m in ms:
                if (m.get("parameter") == parameter and m.get("qualifier") == qualifier
                        and m.get("unitName") == unit and m.get("period") == 900):
                    return m["@id"].rsplit("/", 1)[-1], unit
        return None

    if parameter == "rainfall":
        for m in ms:
            if m.get("parameter") == "rainfall" and m.get("period") == 900:
                return "rain", m["@id"].rsplit("/", 1)[-1], "mm"
        return None
    stage = find("Stage", LEVEL_UNITS)
    if stage:
        return ("level",) + stage
    tidal = find("Tidal Level", TIDAL_UNITS)
    if tidal:
        return ("tidal",) + tidal
    return None


def in_service(station):
    status = str(station.get("status", "")).rsplit("/", 1)[-1]
    return status not in ("statusClosed", "statusSuspended")


def coords(station):
    lat, lon = station.get("lat"), station.get("long")
    if isinstance(lat, list):
        lat, lon = lat[0], lon[0]
    return (lat, lon) if in_region(lat, lon) else None


def stage_scale(ref):
    """Typical range and record high for a river-level station, where published."""
    try:
        item = get(f"/id/stations/{urllib.parse.quote(ref)}")["items"]
    except Exception as e:  # noqa: BLE001 - a missing scale only loses the colouring
        log(f"no stage scale for {ref}: {e}")
        return None, None, None
    scale = item.get("stageScale")
    if not isinstance(scale, dict):
        return None, None, None
    record = scale.get("maxOnRecord")
    record = record.get("value") if isinstance(record, dict) else None
    return number(scale.get("typicalRangeLow")), number(scale.get("typicalRangeHigh")), number(record)


def refresh_stations(con):
    """Rebuild the station list from the API. Run daily; stations change rarely."""
    land, places = load_land(), load_places()
    now = int(time.time())
    seen = []
    for parameter in ("level", "rainfall"):
        items = get("/id/stations", {"parameter": parameter, "lat": CENTRE[0], "long": CENTRE[1],
                                     "dist": RADIUS_KM, "_limit": 2000})["items"]
        for s in items:
            ref, at = s.get("stationReference"), coords(s)
            if not ref or not at or not in_service(s) or not land.keep((at[1], at[0])):
                continue
            picked = pick_measure(s, parameter)
            if not picked:
                continue
            kind, measure, unit = picked
            label = tidy_name(s.get("label", ""))
            if kind == "rain" or not label or label.lower() in ("rainfall station", "rainfall"):
                label = nearest_place(at[0], at[1], places) or f"Rain gauge {ref}"
            low = high = record = None
            if kind == "level":
                low, high, record = stage_scale(ref)
                time.sleep(0.2)
            river = s.get("riverName")
            # Coastal gauges give 'Tide' or 'Sea' as their river; that's no river.
            if not isinstance(river, str) or river.strip().lower() in ("tide", "tidal", "sea"):
                river = None
            con.execute(
                """INSERT INTO stations (id, kind, measure, name, river, lat, lon, unit,
                                         typical_low, typical_high, max_on_record, active, updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, measure=excluded.measure,
                     name=excluded.name, river=excluded.river, lat=excluded.lat, lon=excluded.lon,
                     unit=excluded.unit, typical_low=excluded.typical_low,
                     typical_high=excluded.typical_high, max_on_record=excluded.max_on_record,
                     active=1, updated=excluded.updated""",
                (ref, kind, measure, label, river,
                 round(at[0], 5), round(at[1], 5), unit, low, high, record, now))
            seen.append(ref)
    if seen:
        con.execute(f"UPDATE stations SET active = 0 WHERE id NOT IN ({','.join('?' * len(seen))})", seen)
    con.commit()
    return len(seen)


# ---------------------------------------------------------------- readings

def store(con, measure, rows):
    con.executemany("INSERT OR IGNORE INTO readings (measure, ts, value) VALUES (?, ?, ?)",
                    [(measure, ts, v) for ts, v in rows])


def fetch_series(measure, since_ts):
    """Readings for one series from since_ts onwards. The API keeps about four weeks."""
    items = get(f"/id/measures/{urllib.parse.quote(measure)}/readings",
                {"since": to_iso(since_ts), "_sorted": "", "_limit": 10000})["items"]
    rows = [(to_ts(r["dateTime"]), number(r.get("value"))) for r in items]
    return [(t, v) for t, v in rows if v is not None]


def store_latest(con):
    """Store the newest reading for every station, from one national request
    per parameter. Returns {measure: newest timestamp} for the gap check."""
    measures = {r["measure"] for r in con.execute("SELECT measure FROM stations WHERE active = 1")}
    held = dict(con.execute("SELECT measure, MAX(ts) FROM readings GROUP BY measure").fetchall())
    latest = {}
    for parameter in ("level", "rainfall"):
        for r in get("/data/readings", {"latest": "", "parameter": parameter})["items"]:
            m = r.get("measure", "").rsplit("/", 1)[-1]
            v = number(r.get("value"))
            if m in measures and v is not None and r.get("dateTime"):
                latest[m] = to_ts(r["dateTime"])
                store(con, m, [(latest[m], v)])
    con.commit()
    return latest, held


def fill_gaps(con, latest, held, history_days=28, workers=4):
    """Fetch the history of any station that has readings we don't hold.

    A station whose newest reading is more than one 15-minute interval past
    what we held before this poll has missed readings: we missed polls, or
    the station uploads in batches. Its series is fetched from the last
    reading we held, a few stations at a time.
    """
    floor = int(time.time()) - history_days * 86400
    todo = {m: max(floor, (held[m] + 1) if m in held else floor)
            for m, ts in latest.items() if m not in held or ts - held[m] > 900}
    if not todo:
        return 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_series, m, since): m for m, since in todo.items()}
        for f in as_completed(futures):
            try:
                store(con, futures[f], f.result())
            except Exception as e:  # noqa: BLE001 - keep the rest
                log(f"history fetch failed for {futures[f]}: {e}")
    con.commit()
    return len(todo)
