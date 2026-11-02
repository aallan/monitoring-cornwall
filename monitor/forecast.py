"""Rain and river flow forecasts, fetched on demand and cached.

Forecasts come from Open-Meteo, whose free API is for non-commercial use with
attribution (https://open-meteo.com/), and river flows are scaled with mean
flows from the UK National River Flow Archive. Requests go through the server
so visitors' browsers never contact either, and each answer is cached so
repeated popups don't repeat the request.
"""

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import ea
from .cache import cached, get_json
from .geo import in_region

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"

RAIN_TTL = 15 * 60        # the weather models update hourly at most
FLOW_TTL = 60 * 60        # the flood model updates daily
RAIN_HOURS = 48


def _get(url, params):
    return get_json(url, params)


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

    return cached(("rain", round(lat, 3), round(lon, 3)), RAIN_TTL, fetch)


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
# The flow measured at the same gauging station, every 15 minutes, comes from
# the Environment Agency's Hydrology API; "hydrology" is the station's
# identifier there. Denby's flow measurement is suspended, and the API returns
# no readings for it.
#
# Keyed by the Environment Agency level gauge whose popup shows the forecast.
FLOW_OUTLETS = {
    "47117": {"river": "River Tamar", "nrfa": "47001", "nrfa_name": "Tamar at Gunnislake",
              "cell": (50.525, -4.275), "model_mean": 11.28, "model_q10": 26.35,
              "hydrology": "575d64ff-3205-4213-abe9-ed455d9bc49f"},
    "47136": {"river": "River Lynher", "nrfa": "47004", "nrfa_name": "Lynher at Pillaton Mill",
              "cell": (50.375, -4.275), "model_mean": 4.38, "model_q10": 8.16,
              "hydrology": "817d17c7-3e01-4bc9-a7b3-8e2a5c6d0e5b"},
    "49110": {"river": "River Fowey", "nrfa": "48011", "nrfa_name": "Fowey at Restormel",
              "cell": (50.375, -4.675), "model_mean": 4.52, "model_q10": 8.33,
              "hydrology": "a91dfed0-9614-4cf3-ba5a-d1a17c8c7282"},
    "49133": {"river": "River Camel", "nrfa": "49001", "nrfa_name": "Camel at Denby",
              "cell": (50.525, -4.875), "model_mean": 5.24, "model_q10": 9.62,
              "hydrology": "a987b813-6ebb-44f8-bf5c-658a6a3762a5"},
}

FLOW_PAST_DAYS = 7
FLOW_FORECAST_DAYS = 14

NRFA_URL = "https://nrfaapps.ceh.ac.uk/nrfa/ws/station-info"
NRFA_TTL = 7 * 86400  # the NRFA's API licence allows temporary caching only

HYDROLOGY_URL = "https://environment.data.gov.uk/hydrology/id/measures/{}-flow-i-900-m3s-qualified/readings.json"
MEASURED_TTL = 15 * 60  # a reading every 15 minutes, published within the hour
UK = ZoneInfo("Europe/London")


def gauged_mean(nrfa_id):
    """A station's long-run mean flow in m³/s, from the National River Flow Archive."""
    def fetch():
        d = _get(NRFA_URL, {"station": nrfa_id, "format": "json-object", "fields": "gdf-mean-flow"})
        mean = float(d["data"][0]["gdf-mean-flow"])
        if not mean > 0:
            raise ValueError(f"no mean flow for NRFA station {nrfa_id}")
        return mean

    return cached(("nrfa", nrfa_id), NRFA_TTL, fetch)


def measured(station_id, today=None):
    """Flow measured every 15 minutes since the start of the chart's first
    day, as [[ts, m³/s], ...]. Recent readings are provisional; any the
    Agency has marked suspect are left out. The API gives times in UTC
    without saying so."""
    o = FLOW_OUTLETS[station_id]

    def fetch():
        # The API returns readings after min-date, not on it, and a UK day in
        # summer starts at 23:00 UTC the day before, so ask from two days early.
        start = (today or datetime.now(UK).date()) - timedelta(days=FLOW_PAST_DAYS + 2)
        d = _get(HYDROLOGY_URL.format(o["hydrology"]), {"min-date": start.isoformat(), "_limit": 2000})
        rows = []
        for r in d.get("items", []):
            v = r.get("value")
            if v is None or r.get("quality") == "Suspect":
                continue
            t = datetime.fromisoformat(r["dateTime"].replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            rows.append([int(t.timestamp()), round(float(v), 3)])
        return sorted(rows)

    return cached(("measured", station_id), MEASURED_TTL, fetch)


# GloFAS can run well short of a Cornish river after rain, and its forecast
# carries on from wherever the model is rather than from the river. So the
# forecast is scaled by the ratio of measured to modelled flow on the last
# full day with enough readings, and the model supplies only its shape: how
# the river rises and falls from here. A day older than ANCHOR_DAYS is too
# stale to scale by, and a ratio outside ANCHOR_RANGE says something is wrong
# with one side or the other, so neither is used.
ANCHOR_DAYS = 2
ANCHOR_RANGE = (0.1, 20)
ANCHOR_MIN_READINGS = 48  # half a day of 15-minute readings


def anchor(days, measured_rows, now=None):
    """{"ratio", "day"}: measured over modelled flow on the most recent full
    UK day that has both, or None."""
    now = int(now or time.time())
    complete = [r for r in days if r[0] + 86400 <= now][-ANCHOR_DAYS:]
    for start, model, *_ in reversed(complete):
        v = [m for t, m in measured_rows or [] if start <= t < start + 86400]
        if len(v) < ANCHOR_MIN_READINGS or not model or model <= 0:
            continue
        ratio = sum(v) / len(v) / model
        if ANCHOR_RANGE[0] <= ratio <= ANCHOR_RANGE[1]:
            return {"ratio": round(ratio, 3), "day": start}
        return None
    return None


def flow(station_id):
    """Daily river flow for the past week and the next fortnight, in m³/s:
    {"days": [[ts, mean, min, max], ...], "normal", "high", "measured", ...}.
    Days are UK days, each timestamp the UK midnight it starts at; min and max
    span the forecast ensemble. "measured" is the gauged flow, from measured(),
    and "anchor" what the forecast is scaled by to start from it, from anchor().
    The days are the model's own, unscaled."""
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

    d = dict(cached(("flow", station_id), FLOW_TTL, fetch))
    # The model is the forecast; the measured flow only adds to it, so the
    # chart still draws when the Hydrology API doesn't answer.
    try:
        d["measured"] = measured(station_id)
    except Exception as e:  # noqa: BLE001
        ea.log(f"measured flow for {station_id} unavailable: {e}")
        d["measured"] = None
    d["anchor"] = anchor(d["days"], d["measured"])
    return d
