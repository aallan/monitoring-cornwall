# Monitoring Cornwall

A map of Cornwall's rivers, streams, railways and major roads, built from [OpenStreetMap](https://www.openstreetmap.org/) data. It's a companion to [Floodwatch](https://github.com/aallan/floodwatch), which monitors the River Taw catchment in Devon at [tawriver.watch](https://tawriver.watch).

The site is a single page with no framework and no build step. The server and the data pipeline use only the Python standard library.

## What the map shows

| Layer | OpenStreetMap source | Notes |
|---|---|---|
| County boundary | Relations [57537](https://www.openstreetmap.org/relation/57537) (Cornwall) and [158039](https://www.openstreetmap.org/relation/158039) (Isles of Scilly) | Everything outside the county is dimmed |
| Rivers | `waterway=river` and `waterway=canal` | One line per named river, with flow arrows from zoom 12. Each river ends where it meets the dimmed water outside the county. The two canals that still hold water, the Bude Canal and the Par Canal, are included |
| Streams | `waterway=stream` | Shown from zoom 12 |
| Passenger lines | `railway=rail` | The Cornish Main Line and the Atlantic Coast, Maritime, Looe Valley, Tamar Valley and St Ives Bay lines |
| Freight branches | `railway=rail` with `usage=industrial` or `freight` | The china clay lines around Par and St Blazey |
| Stations | `railway=station` and `railway=halt` | National Rail stations, each with its CRS code |
| Major roads | `highway=trunk`, `primary` or `motorway` with a one- or two-digit A-road `ref` | The A30, A38 and A39, labelled with their numbers. Slip roads are left out |

Every layer is on by default. Hovering over a river, line, road or station shows its name. Each row in the legend switches its layer on and off, and each section heading (Waterways, Railways, Roads) switches the whole section. The page follows the system's light or dark setting, with a toggle to override it.

## Running it

You'll need Python 3.10 or later, `make`, `curl` and `shasum`.

```bash
git clone https://github.com/aallan/monitoring-cornwall.git
cd monitoring-cornwall
make libs     # download Leaflet into site/lib and check it against libs.sha256
make data     # build the overlays in site/data
make serve    # serve the site at http://localhost:8090
```

`make data` takes a few minutes on a first run, most of it waiting on the public [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API) servers. Raw responses are cached in `.cache/overpass/` for a day, so a second run within that window takes seconds. Two flags change that behaviour:

```bash
python3 scripts/fetch_geodata.py --refresh   # ignore the cache and download everything again
python3 scripts/fetch_geodata.py --cached    # rebuild from the cache, however old
```

## Deployment

Monitoring Cornwall runs as a self-contained service on a single server. Nothing generated is committed to this repository: Leaflet and the map overlays are built on the machine that serves the site, by `make libs` and `make data`.

`serve.py` is a small threaded HTTP server that serves `site/` and nothing else. It binds to `localhost` on port 8090 by default; `--bind` and a port argument change both.

```bash
python3 serve.py 8090 --bind ::
```

Every response includes a Content Security Policy that allows scripts, styles and data from the site's own origin, plus basemap tiles from `server.arcgisonline.com`, and nothing else. For a public deployment, put it behind a reverse proxy that terminates TLS, and rerun `make data` from cron to pick up OpenStreetMap edits. Weekly is plenty; rivers and railways change slowly.

## How the overlays are built

`scripts/fetch_geodata.py` queries Overpass for the county boundary, coastline, waterways, railways, stations and major roads, and writes simplified GeoJSON to `site/data/`, along with a `meta.json` that records when the data was downloaded.

Queries select Cornwall by relation ID. The UK county is tagged `Kernow / Cornwall`, and a query on the name `Cornwall` returns Cornwall, Ontario.

OpenStreetMap draws river centrelines down the middle of each estuary to the open sea, which on a map reads as a river flowing across open water. The county boundary follows the low-water line, so it runs up the lower reaches of most estuaries, and the map dims everything outside it. Each river is trimmed where it leaves the boundary, which ends it where it meets that dimmed water. The Fal, for example, ends just above King Harry Ferry rather than running down Carrick Roads to Falmouth Bay.

The Tamar works differently, because its centreline is the boundary. The area query misses most of it, so the script fetches it by name along its whole valley. It never leaves the boundary, so it ends at its tidal limit instead: its `tidal=yes` ways are dropped, and it is cut where it crosses `natural=coastline` at Gunnislake weir.

Railway ways are classified by line name, then by `usage` tag. Heritage and tourist railways are left off the map, as are yards, sidings and crossovers.

## Project layout

```
serve.py                  HTTP server for site/
scripts/fetch_geodata.py  OpenStreetMap to site/data/*.geojson
libs.sha256               checksums for the downloaded Leaflet files
site/index.html           the page
site/css/style.css        layout and theme, light and dark
site/js/map.js            basemap, boundary mask, overlays and legend
site/js/app.js            tab routing and the status line
site/js/utils.js          escaping, formatting and the theme toggle
```

## Contributing

Bug reports, corrections and pull requests are welcome; [CONTRIBUTING.md](CONTRIBUTING.md) covers setup and conventions. Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Licence

The code is released under the [MIT licence](LICENSE).

The map data is © OpenStreetMap contributors, available under the [Open Database Licence](https://www.openstreetmap.org/copyright). Basemap tiles are © [Esri](https://www.esri.com/). [Leaflet](https://leafletjs.com/) is released under the BSD 2-Clause licence.
