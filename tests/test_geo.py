"""Geometry: the region check, line simplification and the land mask."""

import math
import time
import unittest

from monitor.geo import LandMask, in_region, simplify

# A square about 11 km on a side, inland of Bodmin.
SQUARE = [(-4.8, 50.4), (-4.65, 50.4), (-4.65, 50.5), (-4.8, 50.5), (-4.8, 50.4)]


class Region(unittest.TestCase):
    def test_in_region(self):
        self.assertTrue(in_region(50.26, -5.05))           # Truro
        self.assertTrue(in_region(49.92, -6.30))           # St Mary's, Scilly
        self.assertFalse(in_region(51.75, -1.25))          # Oxford
        for bad in (None, "50.3", True, math.nan, math.inf):
            self.assertFalse(in_region(bad, -5.0))


class Simplify(unittest.TestCase):
    def test_drops_points_on_a_straight_line_and_keeps_the_ends(self):
        line = [(0, 0), (1, 0.001), (2, 0), (3, 0)]
        self.assertEqual(simplify(line, 0.01), [(0, 0), (3, 0)])

    def test_keeps_a_corner(self):
        line = [(0, 0), (1, 1), (2, 0)]
        self.assertEqual(simplify(line, 0.01), line)

    def test_short_lines_pass_through(self):
        self.assertEqual(simplify([(0, 0), (1, 1)], 0.5), [(0, 0), (1, 1)])


class Mask(unittest.TestCase):
    def setUp(self):
        self.mask = LandMask([SQUARE], tol_m=500)

    def test_inside_and_outside(self):
        self.assertTrue(self.mask.inside((-4.7, 50.45)))
        self.assertFalse(self.mask.inside((-4.5, 50.45)))
        self.assertFalse(self.mask.inside((math.nan, 50.45)))

    def test_keeps_points_just_outside_the_edge(self):
        just_out = (-4.65 + 300 / (111_320 * math.cos(math.radians(50.45))), 50.45)   # 300 m east
        well_out = (-4.65 + 2000 / (111_320 * math.cos(math.radians(50.45))), 50.45)  # 2 km east
        self.assertTrue(self.mask.keep(just_out))
        self.assertFalse(self.mask.keep(well_out))

    def test_distance(self):
        p = (-4.65 + 1000 / (111_320 * math.cos(math.radians(50.45))), 50.45)
        self.assertAlmostEqual(self.mask.distance_m(p, 3000), 1000, delta=5)
        self.assertEqual(self.mask.distance_m(p, 500), math.inf)

    def test_clip_splits_a_line_at_the_edge(self):
        line = [(-4.75, 50.45), (-4.70, 50.45), (-4.40, 50.45), (-4.30, 50.45)]
        self.assertEqual(self.mask.clip(line), [[(-4.75, 50.45), (-4.70, 50.45)]])

    def test_a_point_near_the_pole_returns_quickly(self):
        start = time.monotonic()
        self.assertFalse(self.mask.near_edge((-4.7, 89.9999999)))
        self.assertEqual(self.mask.distance_m((-4.7, 89.9999999), 1e9), math.inf)
        self.assertLess(time.monotonic() - start, 1.0)


if __name__ == "__main__":
    unittest.main()
