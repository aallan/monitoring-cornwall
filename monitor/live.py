"""The summary the map draws its station markers from: site/data/live.json.

One small file with each station's latest reading and state, so the page can
draw every marker from a single request. History is fetched separately, per
station, when someone opens it.
"""

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "site" / "data" / "live.json"

STALE_AFTER = 3 * 3600     # a reading older than this is shown as out of date
TREND_WINDOW = 3600        # compare with the reading about an hour earlier
TREND_THRESHOLD = 0.02     # metres; smaller changes count as steady


def _trend(con, measure, ts, value):
    row = con.execute(
        "SELECT value FROM readings WHERE measure = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
        (measure, ts - TREND_WINDOW)).fetchone()
    if row is None:
        return None
    change = value - row["value"]
    if change > TREND_THRESHOLD:
        return "rising"
    if change < -TREND_THRESHOLD:
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
            entry["trend"] = _trend(con, s["measure"], ts, value)
            if s["kind"] == "level":
                entry["typical"] = [s["typical_low"], s["typical_high"]]
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
