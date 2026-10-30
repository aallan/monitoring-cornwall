"""Rain and river flow forecasts, fetched on demand and cached.

Forecasts come from Open-Meteo, whose free API is for non-commercial use with
attribution (https://open-meteo.com/), and river flows are scaled with mean
flows from the UK National River Flow Archive. Requests go through the server
so visitors' browsers never contact either, and each answer is cached so
repeated popups don't repeat the request.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .ea import USER_AGENT, log
from .geo import in_region

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"

RAIN_HOURS = 48
RAIN_TTL = 15 * 60        # the weather models update hourly at most
FLOW_TTL = 60 * 60        # the flood model updates daily

_cache = {}
_lock = threading.Lock()


def _get(url, params):
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _cached(key, ttl, fetch):
    """Return a cached value, or fetch and cache it. A failed fetch serves
    the last good value if there is one."""
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        value = fetch()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as e:
        log(f"forecast request failed for {key}: {e}")
        if hit:
            return hit[1]
        raise
    with _lock:
        _cache[key] = (now, value)
    return value


def rain(lat, lon):
    """Hourly rain for the next 48 hours: {"hours": [[ts, mm, probability %], ...]}."""
    if not in_region(lat, lon):
        raise ValueError("outside the area this site covers")

    def fetch():
        d = _get(FORECAST_URL, {
            "latitude": round(lat, 4), "longitude": round(lon, 4),
            "hourly": "precipitation,precipitation_probability",
            "forecast_days": 3, "timezone": "GMT", "timeformat": "unixtime"})
        h = d["hourly"]
        now = int(time.time()) // 3600 * 3600
        rows = [[t, mm, p] for t, mm, p in zip(h["time"], h["precipitation"], h["precipitation_probability"])
                if now <= t < now + RAIN_HOURS * 3600 and mm is not None]
        return {"hours": rows, "source": "Open-Meteo"}

    return _cached(("rain", round(lat, 3), round(lon, 3)), RAIN_TTL, fetch)


# River flow forecasts come from GloFAS, the Copernicus global flood model,
# which routes rivers on a grid of 0.05 degree cells, about 5 km across. At
# that scale a Cornish river often runs through a neighbouring cell rather than
# the one under its gauge, so each outlet names the cell that carries the
# river, found by comparing the model's 2015–2024 flows in the cells around the
# gauge with the gauged record (scripts/calibrate_flow.py). model_mean and
# model_q10 are the model's own mean flow in that cell over those years, and
# the flow it exceeded one day in ten.
#
# Model flows are scaled so their long-run mean matches the gauged mean at the
# National River Flow Archive station, fetched from the NRFA when needed.
# "Normal" is that mean; "high" is the scaled model Q10. The model smooths
# peaks, so its own Q10 is a fairer mark for its forecasts than the gauged one.
#
# Keyed by the Environment Agency level gauge whose popup shows the forecast.
FLOW_OUTLETS = {
    "47117": {"river": "River Tamar", "nrfa": "47001", "nrfa_name": "Tamar at Gunnislake",
              "cell": (50.525, -4.275), "model_mean": 11.28, "model_q10": 26.35},
    "47136": {"river": "River Lynher", "nrfa": "47004", "nrfa_name": "Lynher at Pillaton Mill",
              "cell": (50.375, -4.275), "model_mean": 4.38, "model_q10": 8.16},
    "49110": {"river": "River Fowey", "nrfa": "48011", "nrfa_name": "Fowey at Restormel",
              "cell": (50.375, -4.675), "model_mean": 4.52, "model_q10": 8.33},
    "49133": {"river": "River Camel", "nrfa": "49001", "nrfa_name": "Camel at Denby",
              "cell": (50.525, -4.875), "model_mean": 5.24, "model_q10": 9.62},
}

FLOW_PAST_DAYS = 7
FLOW_FORECAST_DAYS = 14

NRFA_URL = "https://nrfaapps.ceh.ac.uk/nrfa/ws/station-info"
NRFA_TTL = 7 * 86400  # the NRFA's API licence allows temporary caching only


def gauged_mean(nrfa_id):
    """A station's long-run mean flow in m³/s, from the National River Flow Archive."""
    def fetch():
        d = _get(NRFA_URL, {"station": nrfa_id, "format": "json-object", "fields": "gdf-mean-flow"})
        mean = float(d["data"][0]["gdf-mean-flow"])
        if not mean > 0:
            raise ValueError(f"no mean flow for NRFA station {nrfa_id}")
        return mean

    return _cached(("nrfa", nrfa_id), NRFA_TTL, fetch)


def flow(station_id):
    """Daily river flow for the past week and the next fortnight, in m³/s:
    {"days": [[ts, mean, min, max], ...], "normal", "high", ...}. Days are UK
    days, each timestamp the UK midnight it starts at; min and max span the
    forecast ensemble."""
    o = FLOW_OUTLETS[station_id]

    def fetch():
        normal = gauged_mean(o["nrfa"])
        scale = normal / o["model_mean"]
        d = _get(FLOOD_URL, {
            "latitude": o["cell"][0], "longitude": o["cell"][1], "cell_selection": "nearest",
            "daily": "river_discharge_mean,river_discharge_min,river_discharge_max",
            "past_days": FLOW_PAST_DAYS, "forecast_days": FLOW_FORECAST_DAYS,
            "timezone": "Europe/London", "timeformat": "unixtime"})
        dd = d["daily"]
        sc = lambda v: None if v is None else round(v * scale, 2)
        days = [[t, sc(m), sc(lo), sc(hi)]
                for t, m, lo, hi in zip(dd["time"], dd["river_discharge_mean"],
                                        dd["river_discharge_min"], dd["river_discharge_max"])
                if m is not None]
        if not days:
            raise ValueError("no flow data in the response")
        return {
            "river": o["river"], "nrfa": f"{o['nrfa']} {o['nrfa_name']}", "days": days,
            "normal": round(normal, 2), "high": round(o["model_q10"] * scale, 2),
        }

    return _cached(("flow", station_id), FLOW_TTL, fetch)
