"""Environment Agency flood warnings and alerts for Cornwall.

The Agency issues flood warnings (severity 1, severe, and 2) for specific
places, and flood alerts (severity 3) for wider areas. Each is tied to a flood
area with a published outline. Severity 4 means a warning is no longer in
force, and isn't shown.
"""

import json
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .ea import get, log
from .geo import simplify

UK = ZoneInfo("Europe/London")

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "site" / "data" / "warnings.json"

# Areas are listed in a circle around the county, and kept if their county
# list includes Cornwall or Scilly: areas shared with Devon, such as the
# Tamar's, are listed as 'Cornwall, Devon' and a county filter misses them.
CENTRE = (50.40, -4.95)
RADIUS_KM = 110
COUNTIES = ("Cornwall", "Isles of Scilly")

SEVERITY = {1: "Severe flood warning", 2: "Flood warning", 3: "Flood alert"}
SIMPLIFY_TOL = 0.00025  # degrees, about 20-30 m: enough for an outline on the map


def refresh_areas(con):
    """Rebuild the list of flood areas that cover the county. Daily."""
    now = int(time.time())
    items = get("/id/floodAreas", {"lat": CENTRE[0], "long": CENTRE[1], "dist": RADIUS_KM, "_limit": 5000})["items"]
    seen = []
    for a in items:
        county = a.get("county") or ""
        if not a.get("notation") or not any(c in county for c in COUNTIES):
            continue
        # Notations carry the type: FW.. warning areas, WA.. alert areas.
        kind = "alert" if a["notation"][3:5] == "WA" else "warning"
        river = a.get("riverOrSea")
        con.execute(
            """INSERT INTO flood_areas (id, kind, label, description, river, county, active, updated)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, label=excluded.label,
                 description=excluded.description, river=excluded.river, county=excluded.county,
                 active=1, updated=excluded.updated""",
            (a["notation"], kind, a.get("label"), a.get("description"),
             river if isinstance(river, str) else None, county, now))
        seen.append(a["notation"])
    if seen:
        con.execute(f"UPDATE flood_areas SET active = 0 WHERE id NOT IN ({','.join('?' * len(seen))})", seen)
    con.commit()
    return len(seen)


def uk_ts(text):
    """Warning times come without a timezone; read them as UK clock time."""
    if not text:
        return None
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return int((t if t.tzinfo else t.replace(tzinfo=UK)).timestamp())


def _round(ring):
    return [[round(x, 5), round(y, 5)] for x, y in ring]


def fetch_outline(area_id):
    """An area's outline, simplified, as a GeoJSON MultiPolygon geometry."""
    fc = get(f"/id/floodAreas/{urllib.parse.quote(area_id)}/polygon")
    feats = fc.get("features") or [fc]
    polys = []
    for f in feats:
        g = f.get("geometry") or {}
        parts = g.get("coordinates") or []
        if g.get("type") == "Polygon":
            parts = [parts]
        elif g.get("type") != "MultiPolygon":
            continue
        for poly in parts:
            rings = [simplify([tuple(p[:2]) for p in ring], SIMPLIFY_TOL) for ring in poly]
            rings = [_round(r) for r in rings if len(r) >= 4]
            if rings:
                polys.append(rings)
    return {"type": "MultiPolygon", "coordinates": polys} if polys else None


def poll_warnings(con):
    """Warnings and alerts in force in Cornwall's flood areas, with outlines."""
    areas = {r["id"]: r for r in con.execute("SELECT * FROM flood_areas WHERE active = 1")}
    active = []
    for w in get("/id/floods")["items"]:
        aid = w.get("floodAreaID")
        level = w.get("severityLevel")
        if aid not in areas or level not in SEVERITY:
            continue
        area = areas[aid]
        geometry = area["geometry"]
        if geometry is None:
            try:
                outline = fetch_outline(aid)
                geometry = json.dumps(outline, separators=(",", ":")) if outline else None
                con.execute("UPDATE flood_areas SET geometry = ? WHERE id = ?", (geometry, aid))
            except Exception as e:  # noqa: BLE001 - show the warning without its outline
                log(f"flood area outline failed for {aid}: {e}")
        active.append({
            "id": aid, "severity": level, "label": SEVERITY[level],
            "area": w.get("description") or area["label"],
            "description": area["description"], "river": area["river"],
            "message": (w.get("message") or "").strip(),
            "since": uk_ts(w.get("timeSeverityChanged")),   # when it reached this severity
            "updated": uk_ts(w.get("timeMessageChanged")),  # when the message last changed
            "tidal": bool(w.get("isTidal")),
            "geometry": json.loads(geometry) if geometry else None,
        })
    con.commit()
    active.sort(key=lambda w: (w["severity"], w["area"] or ""))
    return active


def write_summary(warnings, now=None):
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"generated": int(now or time.time()), "warnings": warnings},
                              separators=(",", ":"), ensure_ascii=False))
    tmp.replace(SUMMARY)
