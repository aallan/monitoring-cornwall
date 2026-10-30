#!/usr/bin/env python3
"""Draw the site's Open Graph image, site/img/og-image.png (1200 x 630).

The card is HTML, with the county outline and its rivers drawn as SVG from the
map's own overlays in site/data/, so run `make data` first. Google Chrome,
headless, renders it to a PNG.

    python3 scripts/og_image.py
    python3 scripts/og_image.py --html card.html   # write the HTML only

Standard library only.
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from monitor.geo import simplify  # noqa: E402

DATA = ROOT / "site" / "data"
OUT = ROOT / "site" / "img" / "og-image.png"
WIDTH, HEIGHT = 1200, 630
MAP_BOX = (640, 36, 1150, 452)   # left, top, right, bottom
CHROME = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
]

# The site's light-theme colours (site/css/style.css).
BG, TEXT, SECONDARY, ACCENT = "#faf9f7", "#2d2b28", "#7a756d", "#2f7a86"
BANDS = [("Rivers and rain", "#3f7fae"), ("Storm overflows", "#7b3fa0"),
         ("Bathing waters", "#2b8a8f"), ("The sea", "#2f4f73")]


def mercator(lon, lat):
    return lon, math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def lines(fc):
    """Every ring and line in a FeatureCollection, as lists of (lon, lat)."""
    for f in fc["features"]:
        g = f["geometry"]
        parts = {"Polygon": lambda c: c, "MultiPolygon": lambda c: [r for p in c for r in p],
                 "LineString": lambda c: [c], "MultiLineString": lambda c: c}[g["type"]](g["coordinates"])
        yield from parts


def svg_map(boundary, rivers):
    rings = [[mercator(*p[:2]) for p in r] for r in lines(boundary)]
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    left, top, right, bottom = MAP_BOX
    scale = min((right - left) / (max(xs) - min(xs)), (bottom - top) / (max(ys) - min(ys)))
    w, h = (max(xs) - min(xs)) * scale, (max(ys) - min(ys)) * scale
    ox, oy = left + (right - left - w) / 2, top + (bottom - top - h) / 2

    def path(pts, closed):
        pts = simplify([(ox + (x - min(xs)) * scale, oy + (max(ys) - y) * scale) for x, y in pts], 0.5)
        if len(pts) < 2:
            return ""
        return "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + ("Z" if closed else "")

    land = "".join(path(r, True) for r in rings)
    water = "".join(path([mercator(*p[:2]) for p in ln], False) for ln in lines(rivers))
    return (f'<svg width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">'
            f'<path d="{land}" fill="#ffffff" stroke="{ACCENT}" stroke-width="2" stroke-linejoin="round" fill-rule="evenodd"/>'
            f'<path d="{water}" fill="none" stroke="#3f7fae" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
            '</svg>')


def html(boundary, rivers):
    bands = "".join(f'<div style="background:{c}">{name}</div>' for name, c in BANDS)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body {{ margin: 0; width: {WIDTH}px; height: {HEIGHT}px; overflow: hidden; background: {BG}; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; color: {TEXT}; position: relative; }}
  .map {{ position: absolute; inset: 0; }}
  .text {{ position: absolute; left: 80px; top: 112px; width: 600px; }}
  .kicker {{ font-size: 21px; letter-spacing: 0.16em; text-transform: uppercase; color: {SECONDARY}; }}
  h1 {{ font-size: 76px; font-weight: 650; letter-spacing: -0.02em; margin: 26px 0 22px; line-height: 1; }}
  h1 span {{ color: {ACCENT}; }}
  p {{ font-size: 29px; line-height: 1.35; margin: 0; color: #4a4741; }}
  .bands {{ position: absolute; left: 0; right: 0; bottom: 0; height: 150px; display: grid; grid-template-columns: repeat({len(BANDS)}, 1fr); }}
  .bands div {{ display: flex; align-items: center; justify-content: center; color: #fff;
    font-size: 19px; letter-spacing: 0.16em; text-transform: uppercase; }}
</style></head><body>
<div class="map">{svg_map(boundary, rivers)}</div>
<div class="text">
  <div class="kicker">Monitoring Cornwall · Kernow</div>
  <h1>cornwall<span>.</span>watch</h1>
  <p>Rivers, rain, storm overflows, bathing water and the sea around Cornwall, live.</p>
</div>
<div class="bands">{bands}</div>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--html", type=Path, help="write the card's HTML here and stop")
    args = ap.parse_args()
    try:
        boundary = json.loads((DATA / "boundary.geojson").read_text())
        rivers = json.loads((DATA / "rivers.geojson").read_text())
    except FileNotFoundError as e:
        sys.exit(f"{e.filename} is missing; run `make data` first")
    page = html(boundary, rivers)
    if args.html:
        args.html.write_text(page)
        return
    chrome = next((c for c in CHROME if Path(c).exists() or shutil.which(c)), None)
    if not chrome:
        sys.exit("Google Chrome or Chromium is needed to render the PNG; use --html to write the card only")
    with tempfile.TemporaryDirectory() as tmp:
        card = Path(tmp) / "card.html"
        card.write_text(page)
        # A throwaway profile and a stand-in keychain, so Chrome leaves the
        # user's own profile and keychain alone and asks for nothing.
        subprocess.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        f"--user-data-dir={Path(tmp) / 'profile'}", "--use-mock-keychain", "--no-first-run",
                        f"--window-size={WIDTH},{HEIGHT}", f"--screenshot={OUT}", card.as_uri()],
                       check=True, capture_output=True)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
