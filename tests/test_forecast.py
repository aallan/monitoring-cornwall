"""Forecasts: the rain window, scaling modelled river flow, and measured flow."""

import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from monitor import cache, forecast
from tests.support import NOW, QuietTestCase


class Rain(QuietTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_outside_the_region(self):
        with self.assertRaises(ValueError):
            forecast.rain(51.75, -1.25)

    def test_keeps_the_next_48_hours(self):
        hour = NOW // 3600 * 3600
        times = [hour + 3600 * i for i in range(-2, 72)]
        response = {"hourly": {"time": times,
                               "precipitation": [0.1] * 10 + [None] + [0.2] * (len(times) - 11),
                               "precipitation_probability": [40] * len(times)}}
        with mock.patch.object(forecast, "_get", return_value=response), \
                mock.patch.object(forecast.time, "time", return_value=NOW + 120):
            hours = forecast.rain(50.4, -4.9)["hours"]
        self.assertEqual(hours[0][0], hour)
        self.assertLess(hours[-1][0], hour + 48 * 3600)
        self.assertEqual(len(hours), 47)    # 48 hours, less the one with no value
        self.assertEqual(hours[0], [hour, 0.1, 40])


class Flow(QuietTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_scales_the_model_to_the_gauged_mean(self):
        outlet = forecast.FLOW_OUTLETS["47117"]
        gauged = outlet["model_mean"] * 2        # the model has half the river
        response = {"daily": {"time": [NOW, NOW + 86400, NOW + 2 * 86400],
                              "river_discharge_mean": [10.0, None, 12.0],
                              "river_discharge_min": [9.0, None, 8.0],
                              "river_discharge_max": [11.0, None, 20.0]}}
        with mock.patch.object(forecast, "gauged_mean", return_value=gauged), \
                mock.patch.object(forecast, "measured", return_value=[]), \
                mock.patch.object(forecast, "_get", return_value=response) as get:
            d = forecast.flow("47117")
        self.assertEqual(d["days"], [[NOW, 20.0, 18.0, 22.0], [NOW + 2 * 86400, 24.0, 16.0, 40.0]])
        self.assertEqual(d["normal"], round(gauged, 2))
        self.assertEqual(d["high"], round(outlet["model_q10"] * 2, 2))
        params = get.call_args.args[1]
        self.assertEqual((params["latitude"], params["longitude"]), outlet["cell"])
        self.assertEqual(params["cell_selection"], "nearest")

    def test_an_empty_forecast_fails(self):
        response = {"daily": {"time": [NOW], "river_discharge_mean": [None],
                              "river_discharge_min": [None], "river_discharge_max": [None]}}
        with mock.patch.object(forecast, "gauged_mean", return_value=10.0), \
                mock.patch.object(forecast, "measured", return_value=[]), \
                mock.patch.object(forecast, "_get", return_value=response):
            with self.assertRaises(ValueError):
                forecast.flow("47117")

    def test_gauged_mean_comes_from_the_nrfa(self):
        with mock.patch.object(forecast, "_get", return_value={"data": [{"gdf-mean-flow": 23.041}]}) as get:
            self.assertEqual(forecast.gauged_mean("47001"), 23.041)
        self.assertEqual(get.call_args.args[0], forecast.NRFA_URL)

    def test_every_outlet_is_in_the_region(self):
        for sid, o in forecast.FLOW_OUTLETS.items():
            self.assertTrue(forecast.in_region(*o["cell"]), sid)
            self.assertGreater(o["model_q10"], o["model_mean"], sid)


# Six readings from the Fowey at Restormel, trimmed from a real answer, with
# the fifth marked suspect and the sixth's value removed, as a missing one is.
HYDROLOGY = json.loads((Path(__file__).parent / "fixtures" / "hydrology_fowey.json").read_text())
MODEL = {"daily": {"time": [NOW], "river_discharge_mean": [2.0],
                   "river_discharge_min": [1.5], "river_discharge_max": [2.5]}}


class Measured(QuietTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_readings_are_utc_and_suspect_ones_are_dropped(self):
        with mock.patch.object(forecast, "_get", return_value=HYDROLOGY):
            rows = forecast.measured("49110", today=date(2026, 11, 2))
        t0 = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())
        self.assertEqual(rows, [[t0, 3.075], [t0 + 900, 3.051], [t0 + 1800, 3.027], [t0 + 2700, 3.124]])

    def test_asks_for_the_chart_week_and_more(self):
        with mock.patch.object(forecast, "_get", return_value={"items": []}) as get:
            self.assertEqual(forecast.measured("49110", today=date(2026, 11, 2)), [])
        url, params = get.call_args.args
        self.assertIn("a91dfed0-9614-4cf3-ba5a-d1a17c8c7282-flow-i-900-m3s-qualified", url)
        self.assertEqual(params["min-date"], "2026-10-24")   # nine days before

    def test_flow_includes_the_measured_flow(self):
        with mock.patch.object(forecast, "gauged_mean", return_value=4.52), \
                mock.patch.object(forecast, "measured", return_value=[[NOW, 3.0]]), \
                mock.patch.object(forecast, "_get", return_value=MODEL):
            d = forecast.flow("49110")
        self.assertEqual(d["measured"], [[NOW, 3.0]])
        self.assertEqual(d["days"], [[NOW, 2.0, 1.5, 2.5]])

    def test_the_forecast_stands_without_the_measured_flow(self):
        with mock.patch.object(forecast, "gauged_mean", return_value=4.52), \
                mock.patch.object(forecast, "measured", side_effect=TimeoutError("timed out")), \
                mock.patch.object(forecast.ea, "log") as log, \
                mock.patch.object(forecast, "_get", return_value=MODEL):
            d = forecast.flow("49110")
        self.assertIsNone(d["measured"])
        self.assertEqual(d["days"], [[NOW, 2.0, 1.5, 2.5]])
        log.assert_called_once()

    def test_the_cached_model_isnt_changed(self):
        with mock.patch.object(forecast, "gauged_mean", return_value=4.52), \
                mock.patch.object(forecast, "measured", return_value=[[NOW, 3.0]]), \
                mock.patch.object(forecast, "_get", return_value=MODEL):
            forecast.flow("49110")
            with mock.patch.object(forecast, "measured", return_value=[]):
                self.assertEqual(forecast.flow("49110")["measured"], [])

    def test_every_outlet_has_a_hydrology_station(self):
        for sid, o in forecast.FLOW_OUTLETS.items():
            self.assertRegex(o["hydrology"], r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", sid)


if __name__ == "__main__":
    unittest.main()
