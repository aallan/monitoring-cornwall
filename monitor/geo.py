"""Geometry shared by the overlay builder and the station poller."""

import math
from collections import defaultdict


# A box around Cornwall, Scilly and their waters. Coordinates from the feeds
# are checked against it before any geometry runs.
REGION = {"lat": (49.5, 51.5), "lon": (-7.0, -3.5)}


def in_region(lat, lon):
    """True for real numbers inside REGION; False for anything else."""
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lon)):
        return False
    return (math.isfinite(lat) and math.isfinite(lon)
            and REGION["lat"][0] <= lat <= REGION["lat"][1]
            and REGION["lon"][0] <= lon <= REGION["lon"][1])


def perp_dist(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


class LandMask:
    """Point tests against the boundary rings, using a grid index.

    The OSM county boundary follows the low-water line, so the lower reaches
    of most estuaries fall outside it. ``keep()`` is true inside the boundary,
    and also within ``tol_m`` of the boundary line itself, so a river that
    reaches the boundary ends on it rather than just short of it.
    """

    CELL = 0.002  # degrees, ~140 m east-west and ~220 m north-south here
    # The widest search allowed, in cells either side: about 7 km east-west
    # at Cornwall's latitude. Keeps a bad coordinate (one near a pole, where
    # a degree of longitude shrinks towards nothing) from turning a distance
    # search into an endless loop.
    MAX_REACH = 50

    def _reach(self, metres, y):
        """Cells to search either side of a point to cover `metres`."""
        kx = 111_320 * math.cos(math.radians(y))
        ky = 110_570
        if not (math.isfinite(kx) and kx > 1):
            return 0, 0, kx, ky
        rx = min(self.MAX_REACH, math.ceil(metres / (self.CELL * kx)) or 1)
        ry = min(self.MAX_REACH, math.ceil(metres / (self.CELL * ky)) or 1)
        return rx, ry, kx, ky

    def __init__(self, rings, tol_m):
        self.tol_m = tol_m
        self.rows = defaultdict(list)   # y-band -> edges, for ray casting
        self.cells = defaultdict(list)  # (x, y) cell -> edges, for distance
        c = self.CELL
        for ring in rings:
            for a, b in zip(ring, ring[1:]):
                y0, y1 = sorted((a[1], b[1]))
                x0, x1 = sorted((a[0], b[0]))
                for r in range(math.floor(y0 / c), math.floor(y1 / c) + 1):
                    self.rows[r].append((a, b))
                for gx in range(math.floor(x0 / c), math.floor(x1 / c) + 1):
                    for gy in range(math.floor(y0 / c), math.floor(y1 / c) + 1):
                        self.cells[(gx, gy)].append((a, b))

    def inside(self, p):
        x, y = p
        if not (math.isfinite(x) and math.isfinite(y)):
            return False
        crossings = 0
        for (x1, y1), (x2, y2) in self.rows.get(math.floor(y / self.CELL), ()):
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                crossings += 1
        return crossings % 2 == 1

    def near_edge(self, p):
        x, y = p
        if not (math.isfinite(x) and math.isfinite(y)):
            return False
        # Search every cell the tolerance can reach, not just the neighbours.
        rx, ry, kx, ky = self._reach(self.tol_m, y)
        gx, gy = math.floor(x / self.CELL), math.floor(y / self.CELL)
        for dx in range(-rx, rx + 1):
            for dy in range(-ry, ry + 1):
                for a, b in self.cells.get((gx + dx, gy + dy), ()):
                    pa = ((a[0] - x) * kx, (a[1] - y) * ky)
                    pb = ((b[0] - x) * kx, (b[1] - y) * ky)
                    if perp_dist((0.0, 0.0), pa, pb) <= self.tol_m:
                        return True
        return False

    def distance_m(self, p, max_m):
        """Distance in metres from p to the nearest boundary edge, searching
        out to max_m; infinity if there is no edge that close."""
        x, y = p
        if not (math.isfinite(x) and math.isfinite(y)):
            return math.inf
        rx, ry, kx, ky = self._reach(max_m, y)
        gx, gy = math.floor(x / self.CELL), math.floor(y / self.CELL)
        best = math.inf
        for dx in range(-rx, rx + 1):
            for dy in range(-ry, ry + 1):
                for a, b in self.cells.get((gx + dx, gy + dy), ()):
                    pa = ((a[0] - x) * kx, (a[1] - y) * ky)
                    pb = ((b[0] - x) * kx, (b[1] - y) * ky)
                    best = min(best, perp_dist((0.0, 0.0), pa, pb))
        return best if best <= max_m else math.inf

    def keep(self, p):
        return self.inside(p) or self.near_edge(p)

    def clip(self, line):
        """Split a line into the runs that stay on land."""
        runs, cur = [], []
        for p in line:
            if self.keep(p):
                cur.append(p)
            else:
                if len(cur) >= 2:
                    runs.append(cur)
                cur = []
        if len(cur) >= 2:
            runs.append(cur)
        return runs


def simplify(pts, tol):
    """Iterative Douglas-Peucker; keeps endpoints so lines still join."""
    if len(pts) < 3 or tol <= 0:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        best, idx = 0.0, None
        for k in range(i + 1, j):
            d = perp_dist(pts[k], pts[i], pts[j])
            if d > best:
                best, idx = d, k
        if idx is not None and best > tol:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(pts, keep) if k]
