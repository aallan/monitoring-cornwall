"""The summary the map draws its station markers from: site/data/live.json.

One small file with each station's latest reading and state, so the page can
draw every marker from a single request. History is fetched separately, per
station, when someone opens it.
"""

import json
import os
import time
from pathlib import Path

from .forecast import FLOW_OUTLETS

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "site" / "data" / "live.json"

STALE_AFTER = 3 * 3600     # a reading older than this is shown as out of date
TREND_WINDOW = 3600        # the trend is fitted to the last hour of readings
TREND_MIN_READINGS = 3
TREND_THRESHOLD = 0.01     # metres per hour; a gentler slope counts as steady


def _trend(con, measure, ts):
    """Rising, falling or steady, from a straight line fitted through the last
    hour of readings, so it follows the chart's line; None with too few."""
    rows = con.execute("SELECT ts, value FROM readings WHERE measure = ? AND ts > ? AND ts <= ? ORDER BY ts",
                       (measure, ts - TREND_WINDOW, ts)).fetchall()
    if len(rows) < TREND_MIN_READINGS:
        return None
    hours = [(r["ts"] - ts) / 3600 for r in rows]
    values = [r["value"] for r in rows]
    mh, mv = sum(hours) / len(hours), sum(values) / len(values)
    spread = sum((h - mh) ** 2 for h in hours)
    if spread == 0:
        return None
    slope = sum((h - mh) * (v - mv) for h, v in zip(hours, values)) / spread
    if slope > TREND_THRESHOLD:
        return "rising"
    if slope < -TREND_THRESHOLD:
        return "falling"
    return "steady"


def build(con, now=None):
    now = int(now or time.time())
    stations = []
    rows = con.execute("SELECT * FROM stations WHERE active = 1 ORDER BY kind, name").fetchall()
    for s in rows:
        last = con.execute("SELECT ts, value FROM readings WHERE measure = ? ORDER BY ts DESC LIMIT 1",
                           (s["measure"],)).fetchone()
        entry = {"id": s["id"], "kind": s["kind"], "name": s["name"],
                 "lat": s["lat"], "lon": s["lon"], "unit": s["unit"]}
        if s["river"]:
            entry["river"] = s["river"]
        if last is None:
            continue  # listed by the EA but silent for the whole history window
        ts, value = last["ts"], last["value"]
        entry["ts"] = ts
        stale = now - ts > STALE_AFTER
        if s["kind"] == "rain":
            def total(seconds):
                r = con.execute("SELECT COALESCE(SUM(value), 0) FROM readings WHERE measure = ? AND ts > ?",
                                (s["measure"], now - seconds)).fetchone()
                return round(r[0], 1)
            entry["h1"], entry["h24"] = total(3600), total(86400)
            entry["state"] = "stale" if stale else ("raining" if entry["h1"] > 0 else "dry")
        else:
            entry["value"] = round(value, 3)
            entry["trend"] = _trend(con, s["measure"], ts)
            if s["kind"] == "level":
                entry["typical"] = [s["typical_low"], s["typical_high"]]
                if s["id"] in FLOW_OUTLETS:
                    entry["flow"] = True  # the popup offers a river flow forecast
                if stale:
                    entry["state"] = "stale"
                elif s["typical_high"] is not None and value > s["typical_high"]:
                    entry["state"] = "high"
                elif s["typical_low"] is not None and value < s["typical_low"]:
                    entry["state"] = "low"
                elif s["typical_high"] is None:
                    entry["state"] = "unknown"
                else:
                    entry["state"] = "normal"
            else:
                entry["state"] = "stale" if stale else "tidal"
        stations.append(entry)
    return {"generated": now, "stations": stations}


def write(con, now=None):
    """Write live.json atomically, so the server never serves half a file."""
    LIVE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LIVE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(build(con, now), separators=(",", ":"), ensure_ascii=False))
    os.replace(tmp, LIVE)
