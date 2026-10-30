#!/usr/bin/env python3
"""Find the GloFAS model cell that carries a river past a gauging station.

GloFAS routes rivers on 0.05 degree cells, so the cell under a gauge often
holds a side stream. For each National River Flow Archive station given, this
reads the model's daily flows for 2015–2024 in the 5 x 5 cells around the
gauge, from Open-Meteo's flood API, and lists the cells whose mean flow is
closest to the gauged mean, with the model's mean and Q10 (the flow exceeded
one day in ten) for each. The chosen cell, mean and Q10 go into FLOW_OUTLETS
in monitor/forecast.py.

    python3 scripts/calibrate_flow.py 47001 48011

Each station costs Open-Meteo 25 cells x 10 years of data, which counts as many
requests against its hourly limit; the script waits and retries when it hits it.
Standard library only.
"""

import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

NRFA_URL = "https://nrfaapps.ceh.ac.uk/nrfa/ws/station-info"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"
CELL = 0.05
YEARS = ("2015-01-01", "2024-12-31")


def get(url, params):
    for _ in range(5):
        try:
            with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            print("  rate limited; waiting a minute", file=sys.stderr)
            time.sleep(65)
    raise SystemExit("still rate limited; try again in an hour")


def calibrate(nrfa_id):
    s = get(NRFA_URL, {"station": nrfa_id, "format": "json-object",
                       "fields": "id,name,latitude,longitude,gdf-mean-flow"})["data"][0]
    lat, lon, gauged = s["latitude"], s["longitude"], s["gdf-mean-flow"]
    centre = lambda v: math.floor(v / CELL) * CELL + CELL / 2
    cells = [(round(centre(lat) + i * CELL, 3), round(centre(lon) + j * CELL, 3))
             for i in range(-2, 3) for j in range(-2, 3)]
    res = get(FLOOD_URL, {
        "latitude": ",".join(str(c[0]) for c in cells), "longitude": ",".join(str(c[1]) for c in cells),
        "daily": "river_discharge", "start_date": YEARS[0], "end_date": YEARS[1], "cell_selection": "nearest"})
    found = []
    for cell, r in zip(cells, res):
        flows = sorted(v for v in r["daily"]["river_discharge"] if v is not None)
        if not flows:
            continue
        mean = statistics.mean(flows)
        q10 = flows[int(len(flows) * 0.9)]
        km = math.hypot((cell[0] - lat) * 111.2, (cell[1] - lon) * 111.2 * math.cos(math.radians(lat)))
        found.append((abs(math.log(mean / gauged)) if mean > 0 else math.inf, cell, km, mean, q10))
    found.sort()
    print(f"{s['id']} {s['name']}: gauged mean {gauged:.2f} m³/s")
    for _, cell, km, mean, q10 in found[:3]:
        print(f"  cell {cell[0]:.3f}, {cell[1]:.3f}  {km:4.1f} km away  "
              f"model mean {mean:6.2f} ({mean / gauged:.2f} x gauged)  Q10 {q10:6.2f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for i, nrfa_id in enumerate(sys.argv[1:]):
        if i:
            time.sleep(20)
        calibrate(nrfa_id)
