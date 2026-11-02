"""Live power cuts in Cornwall, from National Grid Electricity Distribution.

NGED runs the electricity network in Cornwall and publishes its live power
cuts as open data on its Connected Data portal, under the NGED Open Data
Licence, which is based on the Open Government Licence and asks for the
statement "Supported by NGED Open Data". The file is a CSV of every
unplanned power cut across NGED's four licence areas, each with a position,
the postcodes affected, the customers off and the estimated time power will
be back on. The portal's robots.txt asks crawlers to keep off its API but not
its downloads, so the poller reads the CSV.

Cornwall's power cuts are those whose position passes the same test as the
storm overflows: inside the county, or within 3 km of it and nearer to it
than to Devon. Postcodes won't do, because PL covers Plymouth and west Devon
as well as east Cornwall. NGED's own map leaves out power cuts with nobody
yet confirmed off, which are reports still being looked into, and so does
this one.
"""

import csv
import io
import json
import re
import time
import urllib.request
from pathlib import Path

from . import ea, net, overflows
from .floods import uk_ts

CSV_URL = ("https://connecteddata.nationalgrid.co.uk/dataset/d6672e1e-c684-4cea-bb78-c7e5248b62a2"
           "/resource/292f788f-4339-455b-8cc0-153e14509d4d/download/power_outage_ext.csv")
DATASET = "https://connecteddata.nationalgrid.co.uk/dataset/live-power-cuts"

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "site" / "data" / "powercuts.json"

INCIDENT = re.compile(r"^[A-Za-z0-9-]{1,40}$")
POSTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$")


def number(text):
    try:
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        return 0


def when(text):
    try:
        return uk_ts((text or "").strip() or None)
    except ValueError:
        return None


def parse(text):
    """Every power cut in the file, whatever the area. Times have no
    timezone, and are read as UK clock time."""
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        ident = (r.get("Incident ID") or "").strip()
        try:
            lat, lon = float(r["Location Latitude"]), float(r["Location Longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not INCIDENT.match(ident):
            continue
        start, etr, uploaded = (when(r.get(k)) for k in ("Start Time", "ETR", "Upload Date"))
        postcodes = [p.strip().upper() for p in (r.get("Postcodes") or "").split(",")]
        rows.append({
            "id": ident, "lat": round(lat, 5), "lon": round(lon, 5),
            "off": number(r.get("Confirmed Off")), "predicted": number(r.get("Predicted Off")),
            "restored": number(r.get("Restored")),
            "planned": (r.get("Planned") or "").strip().lower() == "true",
            "category": (r.get("Category") or "").strip()[:40],
            "status": (r.get("Status") or "").strip()[:40],
            "start": start, "etr": etr,
            # The lists repeat some postcodes.
            "postcodes": list(dict.fromkeys(p for p in postcodes if POSTCODE.match(p))),
            "uploaded": uploaded,
        })
    return rows


def cornish(cuts, belongs, places):
    """The power cuts in Cornwall with someone confirmed off, largest first,
    each named after the nearest town or village."""
    keep = [c for c in cuts if c["off"] > 0 and belongs(c["lon"], c["lat"])]
    for c in keep:
        c["place"] = ea.nearest_place(c["lat"], c["lon"], places)
    return sorted(keep, key=lambda c: (-c["off"], c["id"]))


def fetch():
    req = urllib.request.Request(CSV_URL, headers={"User-Agent": ea.USER_AGENT})
    return net.fetch(req).decode("utf-8-sig")


def write_summary(cuts, uploaded, now=None):
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY.with_suffix(".json.tmp")
    for c in cuts:
        c.pop("uploaded", None)
    tmp.write_text(json.dumps({"generated": int(now or time.time()), "updated": uploaded,
                               "source": DATASET, "power_cuts": cuts},
                              separators=(",", ":"), ensure_ascii=False))
    tmp.replace(SUMMARY)


def poll(now=None):
    cuts = parse(fetch())
    uploaded = max((c["uploaded"] for c in cuts if c["uploaded"]), default=None)
    keep = cornish(cuts, overflows.membership(), ea.load_places())
    write_summary(keep, uploaded, now)
    return keep
