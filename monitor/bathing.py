"""Environment Agency bathing waters in Cornwall.

Each designated bathing water has an annual classification (Excellent, Good,
Sufficient or Poor) from four years of samples, weekly samples through the
season, and during the season, May to September, a daily pollution risk
forecast. One request lists all of the county's bathing waters with the
latest of each; a bathing water's sample history is fetched when its popup
opens.
"""

import json
import re
import time
from pathlib import Path

from .cache import cached, get_json
from .floods import uk_ts
from .geo import in_region

LIST_URL = "https://environment.data.gov.uk/doc/bathing-water.json"
SAMPLES_URL = "https://environment.data.gov.uk/doc/bathing-water-quality/in-season/sample.json"
SAMPLING_POINT = "http://location.data.gov.uk/so/ef/SamplingPoint/bwsp.eaew/{}"
DISTRICT = "Cornwall"   # Scilly has no designated bathing waters

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "site" / "data" / "bathing.json"

REFRESH = 3600              # forecasts are published each morning in season
SAMPLES_TTL = 6 * 3600      # samples are taken about weekly
SAMPLES_SHOWN = 25          # about a season and a bit

LATEST = "latestSampleAssessment."
PROPERTIES = ",".join([
    "samplingPoint.lat", "samplingPoint.long", "samplingPoint.samplePointNotation",
    "latestComplianceAssessment.complianceClassification.name",
    "latestRiskPrediction.riskLevel.name", "latestRiskPrediction.expiresAt",
    LATEST + "sampleDateTime.inXSDDateTime",
    LATEST + "escherichiaColiCount", LATEST + "escherichiaColiQualifier.countQualifierNotation",
    LATEST + "intestinalEnterococciCount", LATEST + "intestinalEnterococciQualifier.countQualifierNotation",
])


def value(v):
    """The plain value of a linked-data field: {'_value': x}, [x, ...] or x."""
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, dict):
        v = v.get("_value", v.get("_about"))
    return v


def field(item, path):
    """Follow a dotted path through nested linked-data objects."""
    for key in path.split("."):
        if isinstance(item, list):
            item = item[0] if item else None
        if not isinstance(item, dict):
            return None
        item = item.get(key)
    return value(item)


def year_of(uri):
    m = re.search(r"/year/(\d{4})", str(uri or ""))
    return int(m.group(1)) if m else None


def _number(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse(item):
    """One bathing water from the list, or None if it has no usable position."""
    lat, lon = field(item, "samplingPoint.lat"), field(item, "samplingPoint.long")
    point = field(item, "samplingPoint.samplePointNotation")
    if not point or not re.fullmatch(r"\d{1,8}", str(point)) or not in_region(lat, lon):
        return None
    sample = item.get("latestSampleAssessment")
    return {
        "id": str(point), "eubwid": value(item.get("eubwidNotation")),
        "name": value(item.get("name")) or value(item.get("label")) or f"Bathing water {point}",
        "lat": round(lat, 5), "lon": round(lon, 5),
        "classification": field(item, "latestComplianceAssessment.complianceClassification.name"),
        "class_year": year_of((item.get("latestComplianceAssessment") or {}).get("_about")
                              if isinstance(item.get("latestComplianceAssessment"), dict) else None),
        "risk": field(item, "latestRiskPrediction.riskLevel.name"),
        "risk_expires": uk_ts(field(item, "latestRiskPrediction.expiresAt")),
        "sample_ts": uk_ts(field(sample, "sampleDateTime.inXSDDateTime")) if isinstance(sample, dict) else None,
        "ec": _number(field(sample, "escherichiaColiCount")),
        "ec_q": field(sample, "escherichiaColiQualifier.countQualifierNotation"),
        "ie": _number(field(sample, "intestinalEnterococciCount")),
        "ie_q": field(sample, "intestinalEnterococciQualifier.countQualifierNotation"),
        "rain_affected": 1 if value(item.get("waterQualityImpactedByHeavyRain")) in (True, "true") else 0,
    }


COLUMNS = ("id", "eubwid", "name", "lat", "lon", "classification", "class_year", "risk", "risk_expires",
           "sample_ts", "ec", "ec_q", "ie", "ie_q", "rain_affected")


def refresh(con, now=None):
    """Read every bathing water in the county, with its latest classification,
    forecast and sample. Returns how many were stored."""
    now = int(now or time.time())
    d = get_json(LIST_URL, {"_pageSize": 500, "district.name": DISTRICT, "_properties": PROPERTIES}, timeout=60)
    found = [w for w in (parse(i) for i in d["result"]["items"]) if w]
    for w in found:
        con.execute(
            f"""INSERT INTO bathing_waters ({', '.join(COLUMNS)}, active, updated)
                VALUES ({', '.join('?' * len(COLUMNS))}, 1, ?)
                ON CONFLICT(id) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in COLUMNS[1:])},
                  active=1, updated=excluded.updated""",
            [w[c] for c in COLUMNS] + [now])
    if found:
        ids = [w["id"] for w in found]
        con.execute(f"UPDATE bathing_waters SET active = 0 WHERE id NOT IN ({','.join('?' * len(ids))})", ids)
    con.commit()
    return len(found)


def summary(con, now=None):
    now = int(now or time.time())
    out = []
    for r in con.execute("SELECT * FROM bathing_waters WHERE active = 1 ORDER BY name"):
        entry = {"id": r["id"], "eubwid": r["eubwid"], "name": r["name"], "lat": r["lat"], "lon": r["lon"],
                 "classification": r["classification"], "class_year": r["class_year"]}
        # A forecast stands until it expires; out of season there is none.
        if r["risk"] and r["risk_expires"] and r["risk_expires"] > now:
            entry["risk"] = r["risk"]
        if r["sample_ts"]:
            entry["sample"] = {"ts": r["sample_ts"], "ec": r["ec"], "ec_q": r["ec_q"],
                               "ie": r["ie"], "ie_q": r["ie_q"]}
        if r["rain_affected"]:
            entry["rain_affected"] = True
        out.append(entry)
    return {"generated": now, "bathing_waters": out}


def write_summary(con, now=None):
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary(con, now), separators=(",", ":"), ensure_ascii=False))
    tmp.replace(SUMMARY)


def samples(point):
    """A bathing water's most recent samples, oldest first:
    {"samples": [[ts, E. coli, qualifier, enterococci, qualifier], ...]}."""
    def fetch():
        d = get_json(SAMPLES_URL, {"bwq_samplingPoint": SAMPLING_POINT.format(point),
                                   "_sort": "-sampleDateTime", "_pageSize": SAMPLES_SHOWN}, timeout=30)
        rows = []
        for s in d["result"]["items"]:
            ts = uk_ts(field(s, "sampleDateTime.inXSDDateTime") or _instant(s.get("sampleDateTime")))
            if ts is None:
                continue
            rows.append([ts, _number(value(s.get("escherichiaColiCount"))),
                         field(s, "escherichiaColiQualifier.countQualifierNotation"),
                         _number(value(s.get("intestinalEnterococciCount"))),
                         field(s, "intestinalEnterococciQualifier.countQualifierNotation")])
        rows.sort()
        return {"samples": rows}

    return cached(("bathing", point), SAMPLES_TTL, fetch)


def _instant(v):
    """'http://reference.data.gov.uk/id/gregorian-instant/2026-09-23T08:57:00' -> the time."""
    v = value(v)
    return str(v).rsplit("/", 1)[-1] if v else None
