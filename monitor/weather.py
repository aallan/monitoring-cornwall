"""Met Office weather warnings for Cornwall.

The Met Office publishes its warnings for each region as an RSS feed. Each
item's title gives the level and the weather ('Yellow warning of rain
affecting South West England'), and its description lists the local
authorities it covers and when it is in force:

    Yellow warning of rain affecting South West England: Cornwall, Devon,
    Plymouth, Torbay valid from 0300 Thu 22 Feb to 1400 Thu 22 Feb

The South West England feed covers Bristol to the Isles of Scilly, so only
items that name Cornwall or the Isles of Scilly are kept. The feed's terms
allow any use, with attribution, provided the content and links are shown as
supplied: the site shows each title and description unchanged, with its link.
"""

import email.utils
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import net
from .ea import USER_AGENT

FEED = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/sw"
AREAS = ("Cornwall", "Isles of Scilly")
LEVELS = ("Yellow", "Amber", "Red")
UK = ZoneInfo("Europe/London")

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "site" / "data" / "weather.json"

TITLE = re.compile(r"^(Yellow|Amber|Red) warning of (.+?) affecting ", re.I)
VALID = re.compile(r"valid from (\d{4}) \w{3} (\d{1,2}) (\w{3}) to (\d{4}) \w{3} (\d{1,2}) (\w{3})", re.I)
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def areas(description):
    """The authorities a warning names: after the first colon, up to 'valid from'."""
    head, _, rest = description.partition(":")
    listed = rest.split(" valid from ")[0] if rest else ""
    return [a.strip() for a in re.sub(r"\s+", " ", listed).split(",") if a.strip()]


def valid_times(description, published):
    """(from, to) as Unix seconds. The times are UK clock times without a year,
    which is taken from when the item was published, allowing for a warning
    that runs over the new year."""
    m = VALID.search(re.sub(r"\s+", " ", description))
    if not m:
        return None, None
    pub = datetime.fromtimestamp(published, UK)

    def at(hhmm, day, month):
        mon = MONTHS.get(month.title())
        if mon is None:
            return None
        year = pub.year + (1 if mon < pub.month - 6 else 0) - (1 if mon > pub.month + 6 else 0)
        return int(datetime(year, mon, int(day), int(hhmm[:2]), int(hhmm[2:]), tzinfo=UK).timestamp())

    return at(*m.group(1, 2, 3)), at(*m.group(4, 5, 6))


def text(item, tag):
    el = item.find(tag)
    return (el.text or "").strip() if el is not None else ""


def parse(xml_bytes):
    """Every item in a warnings feed, whatever the area. RSS has no use for a
    DTD, so a document with one is refused rather than risk entity expansion."""
    if b"<!DOCTYPE" in xml_bytes or b"<!ENTITY" in xml_bytes:
        raise ValueError("warnings feed has a DTD")
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        title, description = text(item, "title"), text(item, "description")
        m = TITLE.match(title)
        if not m:
            continue
        pub = text(item, "pubDate")
        try:
            published = int(email.utils.parsedate_to_datetime(pub).timestamp())
        except (TypeError, ValueError):
            published = int(time.time())
        start, end = valid_times(description, published)
        enclosure = item.find("enclosure")
        out.append({
            "level": m.group(1).title(), "weather": m.group(2),
            "title": title, "description": description, "link": text(item, "link"),
            "areas": areas(description), "from": start, "to": end, "published": published,
            "image": enclosure.get("url") if enclosure is not None else None,
        })
    return out


def cornish(warnings, now=None):
    """Those that name Cornwall or Scilly and haven't ended, most severe first."""
    now = int(now or time.time())
    keep = [w for w in warnings if any(a in AREAS for a in w["areas"]) and (w["to"] is None or w["to"] > now)]
    return sorted(keep, key=lambda w: (-LEVELS.index(w["level"]), w["from"] or 0))


def fetch():
    req = urllib.request.Request(FEED, headers={"User-Agent": USER_AGENT})
    return parse(net.fetch(req))


def write_summary(warnings, now=None):
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"generated": int(now or time.time()), "warnings": warnings},
                              separators=(",", ":"), ensure_ascii=False))
    tmp.replace(SUMMARY)


def poll(now=None):
    warnings = cornish(fetch(), now)
    write_summary(warnings, now)
    return warnings
