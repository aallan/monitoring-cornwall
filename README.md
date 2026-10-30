# Monitoring Cornwall

A map of Cornwall's rivers, streams, railways and major roads, built from [OpenStreetMap](https://www.openstreetmap.org/) data, with live readings from the Environment Agency's river level, tidal and rain gauges. It's a companion to [Floodwatch](https://github.com/aallan/floodwatch), which monitors the River Taw catchment in Devon at [tawriver.watch](https://tawriver.watch).

The site is a single page with no framework and no build step. The server, the readings poller and the map data pipeline use only the Python standard library, with readings kept in SQLite.

## What the map shows

Live readings come from the Environment Agency's [real-time flood-monitoring API](https://environment.data.gov.uk/flood-monitoring/doc/reference). Stations take a reading every 15 minutes, which the Environment Agency usually publishes within about half an hour, and the server checks for new ones every 5 minutes.

| Layer | Marker | Notes |
|---|---|---|
| River levels | Circle | Green within the station's typical range, red above it, pale blue below it, and grey where no range is published |
| Tidal levels | Amber diamond | Height in metres above Ordnance Datum where the station reports it |
| Rain gauges | Drop | Filled when rain fell in the last hour. Gauges are named after the nearest village |

A marker turns hollow when its station has sent nothing for three hours. Clicking one opens its latest reading and a chart of the last 24 hours, 7 days or 28 days. A river level's chart starts at zero and runs to 1.25 times the top of its typical range, or higher if the river has been higher in the last 28 days, with a dashed line at the top of the typical range and a dotted one at the record high when that fits. The axis stays the same across all three periods, so they compare by eye. Tidal charts fit the tides recorded, and rain is drawn as totals per 15 minutes, hour or day.

The rest of the map comes from OpenStreetMap.

| Layer | OpenStreetMap source | Notes |
|---|---|---|
| County boundary | Relations [57537](https://www.openstreetmap.org/relation/57537) (Cornwall) and [158039](https://www.openstreetmap.org/relation/158039) (Isles of Scilly) | Everything outside the county is dimmed |
| Rivers | `waterway=river` and `waterway=canal` | One line per named river, with flow arrows from zoom 12. Each river ends where it meets the dimmed water outside the county. The two canals that still hold water, the Bude Canal and the Par Canal, are included |
| Streams | `waterway=stream` | Shown from zoom 12 |
| Passenger lines | `railway=rail` | The Cornish Main Line and the Atlantic Coast, Maritime, Looe Valley, Tamar Valley and St Ives Bay lines |
| Freight branches | `railway=rail` with `usage=industrial` or `freight` | The china clay lines around Par and St Blazey |
| Stations | `railway=station` and `railway=halt` | National Rail stations, each with its CRS code |
| Major roads | `highway=trunk`, `primary` or `motorway` with a one- or two-digit A-road `ref` | The A30, A38 and A39, labelled with their numbers. Slip roads are left out |

Every layer is on by default. Hovering over a river, line, road or station shows its name. Each row in the legend switches its layer on and off, and each section heading (Live readings, Waterways, Railways, Roads) switches the whole section. The page follows the system's light or dark setting, with a toggle to override it.

## Running it

You'll need Python 3.10 or later, `make`, `curl` and `shasum`.

```bash
git clone https://github.com/aallan/monitoring-cornwall.git
cd monitoring-cornwall
make libs     # download Leaflet into site/lib and check it against libs.sha256
make data     # build the overlays in site/data
make serve    # serve the site at http://localhost:8090, polling for readings
```

The server's first poll lists the stations, then fetches 28 days of history for each of them, four at a time, which takes a few minutes; markers appear when it finishes. Later polls take a few seconds. `make poll` runs a single poll without the server.

`make data` takes a few minutes on a first run, most of it waiting on the public [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API) servers. Raw responses are cached in `.cache/overpass/` for a day, so a second run within that window takes seconds. Two flags change that behaviour:

```bash
python3 scripts/fetch_geodata.py --refresh   # ignore the cache and download everything again
python3 scripts/fetch_geodata.py --cached    # rebuild from the cache, however old
```

## Deployment

Monitoring Cornwall runs as one self-contained process on a single server, and its data lives there with it. Nothing generated is committed to this repository: Leaflet and the map overlays are built on the machine that serves the site, by `make libs` and `make data`, and readings collect in `data/monitor.db`.

`serve.py` is a small threaded HTTP server. It serves `site/`, answers `/api/stations/<id>/readings?days=N` from the database, and polls the Environment Agency in a background thread; `--no-poll` turns the poller off where something else runs `python3 -m monitor.poll`. It binds to `localhost` on port 8090 by default; `--bind` and a port argument change both.

```bash
python3 serve.py 8090 --bind ::
```

Every response includes a Content Security Policy that allows scripts, styles and data from the site's own origin, plus basemap tiles from `server.arcgisonline.com`, and nothing else. For a public deployment, put it behind a reverse proxy that terminates TLS, and rerun `make data` from cron to pick up OpenStreetMap edits. Weekly is plenty; rivers and railways change slowly.

Readings are kept indefinitely. 28 days of readings from all the county's stations take about 34 MB, so a year takes roughly 450 MB.

## How live readings are collected

`monitor/` holds the server-side code. Once a day the poller lists the Environment Agency's level and rainfall stations within 110 km of the county, keeps those inside the county boundary or within 1.5 km of it (so the gauges on both banks of the Tamar are included), and reads each river station's published typical range. Stations that have reported nothing in 28 days are left off the map.

Every 5 minutes it makes one request per kind of reading for the latest value from every station in England, keeps Cornwall's, and writes `site/data/live.json`, about 25 KB with every station's latest reading and state, which is everything the map needs to draw its markers. Then it fills gaps. A station whose newest reading is more than 15 minutes ahead of what was stored has missed readings, because the server was down or because the station uploads in batches, so its history is fetched from its last stored reading, four stations at a time. `live.json` is written again once the gaps are filled. A station's history is fetched from `/api/` only when its popup opens.

Each layer of the map draws as soon as its file arrives, without waiting for the others. Streams, the largest overlay, load the first time the map is zoomed in far enough to show them.

## How the overlays are built

`scripts/fetch_geodata.py` queries Overpass for the county boundary, coastline, waterways, railways, stations and major roads, and writes simplified GeoJSON to `site/data/`, along with a `meta.json` that records when the data was downloaded.

Queries select Cornwall by relation ID. The UK county is tagged `Kernow / Cornwall`, and a query on the name `Cornwall` returns Cornwall, Ontario.

OpenStreetMap draws river centrelines down the middle of each estuary to the open sea, which on a map reads as a river flowing across open water. The county boundary follows the low-water line, so it runs up the lower reaches of most estuaries, and the map dims everything outside it. Each river is trimmed where it leaves the boundary, which ends it where it meets that dimmed water. The Fal, for example, ends just above King Harry Ferry rather than running down Carrick Roads to Falmouth Bay.

The Tamar works differently, because its centreline is the boundary. The area query misses most of it, so the script fetches it by name along its whole valley. It never leaves the boundary, so it ends at its tidal limit instead: its `tidal=yes` ways are dropped, and it is cut where it crosses `natural=coastline` at Gunnislake weir.

Railway ways are classified by line name, then by `usage` tag. Heritage and tourist railways are left off the map, as are yards, sidings and crossovers.

## Project layout

```
serve.py                  HTTP server for site/ and /api/, and the poller thread
monitor/ea.py             Environment Agency client: stations, latest readings, history
monitor/poll.py           the 5-minute poll cycle
monitor/live.py           writes site/data/live.json
monitor/db.py             SQLite schema and connection
monitor/geo.py            boundary geometry shared with the overlay builder
scripts/fetch_geodata.py  OpenStreetMap to site/data/*.geojson, and data/places.json
libs.sha256               checksums for the downloaded Leaflet files
site/index.html           the page
site/css/style.css        layout and theme, light and dark
site/js/map.js            basemap, boundary mask, overlays, station markers and legend
site/js/stations.js       station popups and their charts
site/js/app.js            tab routing and the status line
site/js/utils.js          escaping, formatting and the theme toggle
```

## Contributing

Bug reports, corrections and pull requests are welcome; [CONTRIBUTING.md](CONTRIBUTING.md) covers setup and conventions. Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Licence

The code is released under the [MIT licence](LICENSE).

The map data is © OpenStreetMap contributors, available under the [Open Database Licence](https://www.openstreetmap.org/copyright). This uses Environment Agency flood and river level data from the real-time data API (Beta), available under the [Open Government Licence](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). Basemap tiles are © [Esri](https://www.esri.com/). [Leaflet](https://leafletjs.com/) is released under the BSD 2-Clause licence.
