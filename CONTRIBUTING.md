# Contributing to Monitoring Cornwall

Bug reports, corrections and pull requests are all welcome.

## Reporting a problem

Open an [issue](https://github.com/aallan/monitoring-cornwall/issues) and include what you expected to see, what you saw instead, and the steps to reproduce it. For anything on the map, the browser, the zoom level and the place are usually enough, and a screenshot helps.

Missing or misplaced rivers, railways and stations usually come from OpenStreetMap itself. If the map is wrong because the underlying data is wrong, the best fix is an edit on [openstreetmap.org](https://www.openstreetmap.org/), which reaches this map the next time the overlays are rebuilt. Open an issue too if you're unsure which side the problem is on.

## Setting up

You'll need Python 3.10 or later, `make`, `curl` and `shasum`. There are no packages to install.

```bash
git clone https://github.com/aallan/monitoring-cornwall.git
cd monitoring-cornwall
make libs
make data
make serve
```

Then open [http://localhost:8090](http://localhost:8090).

## Making a change

1. Fork the repository and branch from `main`.
2. Keep to the existing style:
   - Python uses the standard library only, with four-space indentation.
   - JavaScript is plain ES2020 in `'use strict'` files, loaded as ordinary script tags, with two-space indentation and single quotes.
   - Colours live as CSS custom properties in `site/css/style.css`, with a value for light and dark mode. The map reads them at render time, so new colours go there rather than into `map.js`.
   - Anything from the data or the user goes through `esc()` before it reaches `innerHTML`.
   - Event handlers are attached with `addEventListener`. The Content Security Policy blocks inline handlers such as `onclick=`.
   - Copy and comments are in British English.
3. Check the change in a browser at desktop and phone widths, in light and dark mode. If it touches `scripts/fetch_geodata.py`, run `make data` and look at the result on the map.
4. When a change alters a JavaScript or CSS file, bump its `?v=` query string in `site/index.html`, so browsers fetch the new version.
5. Open a pull request against `main` that says what changed and why.

## What not to send

- Generated files. `site/lib/`, `site/data/` and `.cache/` are built on the machine that serves the site and stay out of the repository.
- Frameworks, bundlers or package managers. The page is plain HTML, CSS and JavaScript with no build step, and the Python has no third-party dependencies.
- Scripts, styles or fonts from a CDN. The Content Security Policy only allows the site's own origin and the basemap tile server; a new library is downloaded by `make libs` and pinned in `libs.sha256`.
- Credentials, API keys or personal data of any kind.

## Code of conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).
