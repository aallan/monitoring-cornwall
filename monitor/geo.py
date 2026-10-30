"""Geometry shared by the overlay builder and the station poller."""

import math
from collections import defaultdict


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
        crossings = 0
        for (x1, y1), (x2, y2) in self.rows.get(math.floor(y / self.CELL), ()):
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                crossings += 1
        return crossings % 2 == 1

    def near_edge(self, p):
        x, y = p
        kx = 111_320 * math.cos(math.radians(y))  # metres per degree
        ky = 110_570
        gx, gy = math.floor(x / self.CELL), math.floor(y / self.CELL)
        # Search every cell the tolerance can reach, not just the neighbours.
        rx = math.ceil(self.tol_m / (self.CELL * kx)) or 1
        ry = math.ceil(self.tol_m / (self.CELL * ky)) or 1
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
        kx = 111_320 * math.cos(math.radians(y))
        ky = 110_570
        gx, gy = math.floor(x / self.CELL), math.floor(y / self.CELL)
        rx = math.ceil(max_m / (self.CELL * kx)) or 1
        ry = math.ceil(max_m / (self.CELL * ky)) or 1
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
