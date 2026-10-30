#!/usr/bin/env python3
"""Fetch Cornwall's boundary, waterways, railways and major roads from OpenStreetMap.

Queries the Overpass API and writes simplified GeoJSON overlays to site/data/:

    boundary.geojson   Cornwall + Isles of Scilly (multipolygon)
    rivers.geojson     rivers and canals, one feature per named river
    streams.geojson    streams (minor waterways), simplified
    railways.geojson   running lines, classified passenger or freight
    roads.geojson      major roads (one- and two-digit A roads)
    stations.geojson   National Rail stations
    meta.json          when the snapshot was taken, and feature counts

and, for the server only, data/places.json (the county's towns, villages and
hamlets, used to name rain gauges) and data/neighbours.geojson (Devon and
Plymouth, used to place offshore storm overflows on the right side of the
Tamar).

Standard library only. Run occasionally (``make data``; add ``--refresh`` to bypass the cache, or
``--cached`` to reprocess the cached downloads without fetching); the overlays change
slowly, so the site serves these static snapshots rather than querying live.
"""

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from monitor.geo import LandMask, simplify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data"
SERVER_DATA = ROOT / "data"  # server-side files, not served to browsers
# Raw Overpass responses, so a failure late in a run doesn't mean
# re-downloading everything. Reused for a day unless --refresh is given.
CACHE = ROOT / ".cache" / "overpass"
CACHE_MAX_AGE = 24 * 3600
REFRESH = "--refresh" in sys.argv
CACHED_ONLY = "--cached" in sys.argv  # reprocess from the cache, however old
FETCHED = []  # when each dataset was downloaded; the oldest dates the snapshot

# OSM relation IDs. Matching on name is unsafe: "Cornwall" also finds
# Cornwall, Ontario (relation 4136858), and the UK relation is tagged
# "Kernow / Cornwall".
CORNWALL = 57537   # ref:gss E06000052
SCILLY = 158039    # ref:gss E06000053

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
USER_AGENT = "monitoring-cornwall/0.1 (+https://github.com/aallan/monitoring-cornwall)"

AREA = f"rel({CORNWALL});map_to_area->.c;"

# Simplification tolerance in degrees (~1e-4 deg is ~7-11 m in Cornwall).
SIMPLIFY_RIVER = 0.00004
SIMPLIFY_STREAM = 0.00012
SIMPLIFY_RAIL = 0.00003
SIMPLIFY_BOUNDARY = 0.0001
# Rivers end where they leave the county's low-water boundary. Points within
# this distance of the boundary line count as inside it.
ESTUARY_TOLERANCE_M = 60
# Rivers that form the county boundary. They wander either side of the
# boundary line, so they skip the boundary check and end at their tidal limit.
BORDER_RIVERS = {"River Tamar"}
PRECISION = 5  # decimal places, ~1 m

# Railway classification. Way names in OSM carry the line name.
PASSENGER_LINES = {
    "Cornish Main Line": "Cornish Main Line",
    "The Cornish Main Line": "Cornish Main Line",
    "The Atlantic Coast Line": "Atlantic Coast Line",
    "The Maritime Line": "Maritime Line",
    "Looe Valley Line": "Looe Valley Line",
    "Tamar Valley Line": "Tamar Valley Line",
    "The St Ives Bay Line": "St Ives Bay Line",
}
# Heritage and tourist lines are left off the map; listed so their ways are
# recognised and dropped rather than mistaken for freight.
HERITAGE_LINES = {
    "Bodmin & Wenford Railway": "Bodmin & Wenford Railway",
    "Launceston Steam Railway": "Launceston Steam Railway",
    "Lappa Valley Steam Railway": "Lappa Valley Railway",
    "Helston Railway": "Helston Railway",
}
FREIGHT_LINES = {
    "Fowey Branch": "Fowey Branch",
    "The Drinnick Mill Line": "Drinnick Mill Line",
    "Newlyn Downs Branch Line": "Newlyn Downs Branch",
    "Par Harbour Branch": "Par Harbour Branch",
    "Goonbarrow Branch": "Goonbarrow Branch",
    "Pontsmill Branch": "Pontsmill Branch",
    "Falmouth Docks": "Falmouth Docks",
}
SKIP_SERVICE = {"yard", "siding", "crossover"}


# --------------------------------------------------------------------------
# Overpass
# --------------------------------------------------------------------------

def overpass(query, label):
    """POST a query, retrying with exponential backoff across endpoints."""
    cached = CACHE / f"{label}.json"
    fresh = cached.exists() and (CACHED_ONLY or time.time() - cached.stat().st_mtime < CACHE_MAX_AGE)
    if not REFRESH and fresh:
        els = json.loads(cached.read_text())
        FETCHED.append(cached.stat().st_mtime)
        print(f"  {label}: {len(els)} elements (cached)")
        return els
    data = urllib.parse.urlencode({"data": query}).encode()
    delay = 5
    for attempt in range(8):
        url = ENDPOINTS[attempt % len(ENDPOINTS)]
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                body = json.loads(r.read())
            remark = body.get("remark", "")
            if "runtime error" in remark or "timed out" in remark:
                raise RuntimeError(remark)
            print(f"  {label}: {len(body['elements'])} elements ({urllib.parse.urlparse(url).netloc})")
            FETCHED.append(time.time())
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(body["elements"]))
            return body["elements"]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as e:
            print(f"  {label}: attempt {attempt + 1} failed ({e}); retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 120)
    sys.exit(f"Overpass query failed after retries: {label}")


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------

def pause():
    """Be polite between live queries; no need when reading the cache."""
    if not CACHED_ONLY:
        time.sleep(3)


def coords_of(way):
    return [(p["lon"], p["lat"]) for p in way.get("geometry", []) if p]


def rnd(pts):
    return [[round(x, PRECISION), round(y, PRECISION)] for x, y in pts]


def length_km(pts):
    total = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        # Equirectangular is plenty at this scale.
        kx = 111.32 * math.cos(math.radians((y1 + y2) / 2))
        total += math.hypot((x2 - x1) * kx, (y2 - y1) * 110.57)
    return total


def merge_lines(lines):
    """Join line strings end-to-start where they meet, keeping direction.

    OSM waterways are drawn source-to-mouth, so joining only end->start keeps
    the flow direction intact for the arrows on the map.
    """
    lines = [list(l) for l in lines if len(l) >= 2]
    merged = True
    while merged:
        merged = False
        starts = {}
        for i, l in enumerate(lines):
            starts.setdefault(l[0], []).append(i)
        used = set()
        out = []
        for i, l in enumerate(lines):
            if i in used:
                continue
            used.add(i)
            cur = l
            while True:
                nxt = [j for j in starts.get(cur[-1], []) if j not in used]
                if not nxt:
                    break
                j = nxt[0]
                used.add(j)
                cur = cur + lines[j][1:]
                merged = True
            out.append(cur)
        lines = out
    return lines


def assemble_rings(ways):
    """Stitch boundary member ways (in any order/direction) into closed rings."""
    segs = [coords_of(w) for w in ways]
    segs = [s for s in segs if len(s) >= 2]
    rings = []
    while segs:
        ring = segs.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[-2::-1]
                elif s[-1] == ring[0]:
                    ring = s[:-1] + ring
                elif s[0] == ring[0]:
                    ring = s[:0:-1] + ring
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
        else:
            print(f"  warning: dropped an unclosed boundary ring ({len(ring)} points)", file=sys.stderr)
    return rings


class Coastline:
    """Cuts a river line where it first crosses the coastline.

    In UK OSM the coastline runs up each estuary to about the tidal limit and
    closes across the river there, so the first crossing on a line drawn
    source-to-mouth is where the river meets tidal water.
    """

    CELL = 0.002

    def __init__(self, ways):
        self.cells = defaultdict(list)
        c = self.CELL
        for w in ways:
            g = coords_of(w)
            for a, b in zip(g, g[1:]):
                for gx in range(math.floor(min(a[0], b[0]) / c), math.floor(max(a[0], b[0]) / c) + 1):
                    for gy in range(math.floor(min(a[1], b[1]) / c), math.floor(max(a[1], b[1]) / c) + 1):
                        self.cells[(gx, gy)].append((a, b))

    @staticmethod
    def _cross(p, q, a, b):
        """Fraction along p->q where it meets a->b, or None."""
        rx, ry = q[0] - p[0], q[1] - p[1]
        sx, sy = b[0] - a[0], b[1] - a[1]
        den = rx * sy - ry * sx
        if den == 0:
            return None
        t = ((a[0] - p[0]) * sy - (a[1] - p[1]) * sx) / den
        u = ((a[0] - p[0]) * ry - (a[1] - p[1]) * rx) / den
        return t if 0 <= t <= 1 and 0 <= u <= 1 else None

    def cut(self, line):
        c = self.CELL
        for i, (p, q) in enumerate(zip(line, line[1:])):
            near = set()
            for gx in range(math.floor(min(p[0], q[0]) / c), math.floor(max(p[0], q[0]) / c) + 1):
                for gy in range(math.floor(min(p[1], q[1]) / c), math.floor(max(p[1], q[1]) / c) + 1):
                    near.update(self.cells.get((gx, gy), ()))
            ts = [t for a, b in near
                  if (t := self._cross(p, q, a, b)) is not None and not (i == 0 and t == 0)]
            if ts:
                t = min(ts)
                end = (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
                return line[:i + 1] + [end]
        return line


def feature(geom_type, coords, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": geom_type, "coordinates": coords}}


def write(name, features):
    path = OUT / name
    fc = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(fc, separators=(",", ":"), ensure_ascii=False))
    print(f"  wrote {path.relative_to(OUT.parent.parent)} ({len(features)} features, {path.stat().st_size // 1024} KB)")


# --------------------------------------------------------------------------
# Layers
# --------------------------------------------------------------------------

def fetch_boundary():
    """Return the boundary feature, plus the full-resolution rings for clipping."""
    els = overpass(f"[out:json][timeout:180];rel(id:{CORNWALL},{SCILLY});out geom;", "boundary")
    polys, rings = [], []
    for rel in els:
        outer = [m for m in rel.get("members", []) if m["type"] == "way" and m.get("role") in ("outer", "")]
        for ring in assemble_rings(outer):
            rings.append(ring)
            ring = simplify(ring, SIMPLIFY_BOUNDARY)
            if len(ring) >= 4:
                polys.append([rnd(ring)])
    return [feature("MultiPolygon", polys, name="Cornwall and the Isles of Scilly")], rings


def river_name(raw):
    """Normalise names so tidal reaches group with their river.

    e.g. 'River Lynher or St Germans River' -> 'River Lynher', 'Neet' -> 'River Neet'.
    """
    name = raw.split(" or ")[0].strip()
    if name == "Neet":
        name = "River Neet"
    return name


def fetch_waterways(land, coast):
    els = overpass(
        f'[out:json][timeout:240];{AREA}way(area.c)["waterway"~"^(river|canal)$"];out tags geom;',
        "rivers")
    # The Tamar's centreline is the county boundary, so the area filter above
    # misses most of it. Fetch it by name along its whole valley instead.
    pause()
    seen = {w["id"] for w in els}
    els += [w for w in overpass(
        '[out:json][timeout:180];way["waterway"="river"]["name"~"^River Tamar"](50.30,-4.70,50.95,-4.10);out tags geom;',
        "tamar") if w["id"] not in seen]
    by_name = defaultdict(list)
    kind_of = {}
    for w in els:
        t = w.get("tags", {})
        # Drop disused canal remnants; Cornwall's watered canals are the Bude
        # Canal and the Par Canal.
        if t.get("waterway") == "canal" and (t.get("disused") or t.get("abandoned") or t.get("usage") == "disused"):
            continue
        name = river_name(t["name"]) if t.get("name") else ""
        if name in BORDER_RIVERS and t.get("tidal") == "yes":
            continue
        by_name[name].append(coords_of(w))
        kind_of.setdefault(name, t.get("waterway"))

    rivers = []
    for name, lines in by_name.items():
        # OSM carries river centrelines down the middle of estuaries to the
        # sea. Each river ends where it meets the water outside the county's
        # low-water boundary, which the map dims. A border river runs along
        # that boundary instead of meeting it, so it ends at its tidal limit:
        # its tidal ways are dropped above, and it is cut at the coastline.
        if name in BORDER_RIVERS:
            merged = [coast.cut(l) for l in merge_lines(lines)]
        else:
            merged = [run for l in merge_lines(lines) for run in land.clip(l)]
        if not merged:
            continue
        parts = [rnd(simplify(l, SIMPLIFY_RIVER)) for l in merged]
        km = sum(length_km(l) for l in merged)
        rivers.append(feature("MultiLineString", parts, name=name, kind=kind_of[name], length_km=round(km, 1)))
    rivers.sort(key=lambda f: -f["properties"]["length_km"])

    pause()
    els = overpass(f'[out:json][timeout:240];{AREA}way(area.c)["waterway"="stream"];out tags geom;', "streams")
    named = defaultdict(list)
    unnamed = []
    for w in els:
        nm = w.get("tags", {}).get("name", "")
        (named[nm] if nm else unnamed).append(coords_of(w))
    streams = []
    for nm, lines in named.items():
        parts = [rnd(simplify(l, SIMPLIFY_STREAM)) for l in merge_lines(lines)]
        streams.append(feature("MultiLineString", parts, name=nm))
    # Unnamed streams go out as one feature: thousands of tiny ways otherwise.
    parts = [rnd(simplify(l, SIMPLIFY_STREAM)) for l in merge_lines(unnamed)]
    streams.append(feature("MultiLineString", parts, name=""))
    return rivers, streams


def classify_rail(t):
    """Return (kind, line) for a railway way, or None to skip it."""
    if t.get("service") in SKIP_SERVICE:
        return None
    railway, usage, name = t.get("railway"), t.get("usage"), t.get("name", "")
    if name in HERITAGE_LINES:
        return None
    if name in FREIGHT_LINES:
        return "freight", FREIGHT_LINES[name]
    if name in PASSENGER_LINES:
        return "passenger", PASSENGER_LINES[name]
    if railway in ("preserved", "narrow_gauge") or usage == "tourism":
        return None
    if usage in ("industrial", "freight", "military"):
        return "freight", ""
    if railway == "rail" and usage in ("main", "branch"):
        return "passenger", ""
    if railway == "rail":
        return "freight", ""
    return None


def fetch_railways():
    els = overpass(
        f'[out:json][timeout:240];{AREA}way(area.c)["railway"~"^(rail|preserved|narrow_gauge)$"];out tags geom;',
        "railways")
    groups = defaultdict(list)
    for w in els:
        c = classify_rail(w.get("tags", {}))
        if c:
            groups[c].append(coords_of(w))
    rails = []
    for (kind, line), lines in sorted(groups.items()):
        merged = merge_lines(lines)
        parts = [rnd(simplify(l, SIMPLIFY_RAIL)) for l in merged]
        rails.append(feature("MultiLineString", parts, kind=kind, line=line,
                             length_km=round(sum(length_km(l) for l in merged), 1)))

    pause()
    els = overpass(
        f'[out:json][timeout:180];{AREA}node(area.c)["railway"~"^(station|halt)$"];out body;', "stations")
    stations = []
    for n in els:
        t = n.get("tags", {})
        if not t.get("name") or "disused" in t or t.get("railway:historic"):
            continue
        # National Rail stations only; heritage and miniature railway halts are skipped.
        if not ("ref:crs" in t or t.get("network") == "National Rail"):
            continue
        stations.append(feature("Point", [round(n["lon"], PRECISION), round(n["lat"], PRECISION)],
                                name=t["name"], crs=t.get("ref:crs", ""), operator=t.get("operator", "")))
    stations.sort(key=lambda f: f["properties"]["name"])
    return rails, stations


def fetch_roads():
    """The county's major roads: A roads with one- or two-digit numbers.

    In Cornwall these are the A30, A38 and A39. Slip roads (``*_link``) are
    left out. Dual carriageways stay as two lines, as OSM draws them.
    """
    els = overpass(
        f'[out:json][timeout:240];{AREA}way(area.c)["highway"~"^(motorway|trunk|primary)$"]'
        '["ref"~"(^|;) *A[0-9]{1,2}( *;|$)"];out tags geom;', "roads")
    by_ref = defaultdict(list)
    for w in els:
        refs = [r.strip() for r in w.get("tags", {}).get("ref", "").split(";")]
        major = [r for r in refs if r[:1] == "A" and r[1:].isdigit() and len(r) <= 3]
        if major:
            by_ref[major[0]].append(coords_of(w))
    roads = []
    for ref, lines in sorted(by_ref.items()):
        merged = merge_lines(lines)
        parts = [rnd(simplify(l, SIMPLIFY_RAIL)) for l in merged]
        roads.append(feature("MultiLineString", parts, ref=ref,
                             length_km=round(sum(length_km(l) for l in merged), 1)))
    return roads


# Cornwall's neighbours across the Tamar, for deciding which county an
# offshore storm overflow belongs to: Devon (county) and Plymouth (unitary).
NEIGHBOURS_GSS = ("E10000008", "E06000026")


def fetch_neighbours():
    """Outer rings of the neighbouring authorities."""
    els = overpass(
        '[out:json][timeout:240];rel["ref:gss"~"^(' + "|".join(NEIGHBOURS_GSS) + ')$"];out geom;',
        "neighbours")
    polys = []
    for rel in els:
        outer = [m for m in rel.get("members", []) if m["type"] == "way" and m.get("role") in ("outer", "")]
        for ring in assemble_rings(outer):
            ring = simplify(ring, SIMPLIFY_BOUNDARY)
            if len(ring) >= 4:
                polys.append([rnd(ring)])
    return [feature("MultiPolygon", polys, name="Devon and Plymouth")]


def fetch_places():
    """Towns, villages and hamlets, used to name the rain gauges."""
    els = overpass(
        f'[out:json][timeout:180];{AREA}node(area.c)["place"~"^(town|village|hamlet)$"]["name"];out body;',
        "places")
    return [{"name": n["tags"]["name"], "place": n["tags"]["place"],
             "lat": round(n["lat"], PRECISION), "lon": round(n["lon"], PRECISION)} for n in els]


def main():
    sys.stdout.reconfigure(line_buffering=True)  # show progress when piped
    OUT.mkdir(parents=True, exist_ok=True)
    print("Fetching from OpenStreetMap (Overpass API)…")
    boundary, rings = fetch_boundary()
    land = LandMask(rings, ESTUARY_TOLERANCE_M)
    pause()
    coast = Coastline(overpass(
        f'[out:json][timeout:240];{AREA}way(area.c)["natural"="coastline"];out geom;', "coastline"))
    pause()
    rivers, streams = fetch_waterways(land, coast)
    pause()
    rails, stations = fetch_railways()
    pause()
    roads = fetch_roads()
    pause()
    places = fetch_places()
    pause()
    neighbours = fetch_neighbours()

    print("Writing overlays…")
    write("boundary.geojson", boundary)
    write("rivers.geojson", rivers)
    write("streams.geojson", streams)
    write("railways.geojson", rails)
    write("roads.geojson", roads)
    SERVER_DATA.mkdir(exist_ok=True)
    (SERVER_DATA / "places.json").write_text(json.dumps(places, ensure_ascii=False))
    (SERVER_DATA / "neighbours.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": neighbours}, separators=(",", ":")))
    print(f"  wrote data/places.json ({len(places)} places)")
    write("stations.geojson", stations)
    meta = {
        "generated": datetime.fromtimestamp(min(FETCHED), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "OpenStreetMap contributors, via the Overpass API (ODbL)",
        "counts": {
            "rivers": len([f for f in rivers if f["properties"]["name"]]),
            "stations": len(stations),
        },
    }
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print("Done.")


if __name__ == "__main__":
    main()
