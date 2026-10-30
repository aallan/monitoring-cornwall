# Security policy

## Supported versions

Only the latest commit on `main` is maintained.

## Reporting a vulnerability

Please report security problems privately rather than in a public issue.

1. Email alasdair@babilim.co.uk with a description of the problem and the steps to reproduce it.
2. Allow reasonable time for it to be assessed and fixed before disclosing it publicly.

You'll get an acknowledgement within 48 hours, and a follow-up once the report has been triaged.

## Scope

Monitoring Cornwall is a map with read-only live data. Visitors can't submit anything, and there are no accounts or cookies. The surface that matters is:

- `serve.py`, which serves files from `site/` only, refuses directory listings, and binds to `localhost` unless told otherwise
- the `/api/stations/<id>/readings` endpoint, which validates the station ID against a fixed pattern, caps the history at 28 days, and reads the database through a query-only connection
- the poller, which reads from the Environment Agency's public API and writes readings to `data/monitor.db`
- the Content Security Policy sent with every response, which allows scripts and data only from the site's own origin, images from that origin and the basemap tile server, and inline styles, which Leaflet uses to position the map
- rendering of OpenStreetMap and Environment Agency names in the page, which are escaped or set as text before they reach the DOM
- `scripts/fetch_geodata.py`, which reads responses from the public Overpass API when the overlays are built
- the Leaflet download in `make libs`, which is checked against the SHA-256 checksums in `libs.sha256`

## Out of scope

- The OpenStreetMap data itself; fix errors at [openstreetmap.org](https://www.openstreetmap.org/)
- The Overpass API servers
- The Environment Agency's flood-monitoring API
- Esri's basemap tile service
- Vulnerabilities in Leaflet itself; report those to the [Leaflet project](https://github.com/Leaflet/Leaflet/security)
- The configuration of any reverse proxy or server that someone runs this behind
