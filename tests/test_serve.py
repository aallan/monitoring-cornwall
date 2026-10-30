"""The HTTP server: headers, static files and the /api/ endpoints."""

import http.client
import json
import threading
import time
import unittest
from functools import partial
from unittest import mock

import serve
from monitor import bathing, forecast
from tests.support import NOW, DBTestCase


class Server(DBTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(serve.Handler, "log_message")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.httpd = serve.Server(("127.0.0.1", 0), partial(serve.Handler, directory=str(serve.SITE)))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]

    def request(self, path, method="GET"):
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            con.request(method, path)
            r = con.getresponse()
            body = r.read()
            return r.status, r, body
        finally:
            con.close()

    def get_json(self, path):
        status, _, body = self.request(path)
        return status, json.loads(body)


class Static(Server):
    def test_page_has_security_headers(self):
        status, r, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Monitoring Cornwall", body)
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")

    def test_no_directory_listings(self):
        self.assertEqual(self.request("/js/")[0], 404)

    def test_no_files_outside_the_site(self):
        for path in ("/../serve.py", "/%2e%2e/serve.py", "/..%2fserve.py"):
            status, _, body = self.request(path)
            self.assertNotIn(b"ThreadingHTTPServer", body, path)

    def test_head_on_the_api_is_refused(self):
        self.assertEqual(self.request("/api/stations/A/readings", "HEAD")[0], 405)


class Readings(Server):
    def setUp(self):
        super().setUp()
        m = self.add_station("A")
        now = int(time.time())
        self.add_readings(m, [(now - 86400 * d, 0.5) for d in (0, 5, 40)])

    def test_readings(self):
        status, d = self.get_json("/api/stations/A/readings?days=7")
        self.assertEqual(status, 200)
        self.assertEqual((d["days"], len(d["readings"]), d["typical"], d["record"]), (7, 2, [0.2, 1.0], 2.0))

    def test_days_are_capped_and_defaulted(self):
        self.assertEqual(self.get_json("/api/stations/A/readings?days=9999")[1]["days"], serve.MAX_HISTORY_DAYS)
        self.assertEqual(self.get_json("/api/stations/A/readings?days=-3")[1]["days"], 1)
        self.assertEqual(self.get_json("/api/stations/A/readings?days=lots")[1]["days"], 7)

    def test_unknown_and_malformed_ids(self):
        self.assertEqual(self.request("/api/stations/NOPE/readings")[0], 404)
        self.assertEqual(self.request("/api/stations/a%27%20OR%201=1/readings")[0], 404)
        self.assertEqual(self.request("/api/stations/" + "x" * 40 + "/readings")[0], 404)
        self.assertEqual(self.request("/api/nothing")[0], 404)


class Overflows(Server):
    def test_overflow_detail(self):
        self.add_overflow("SBB00407")
        status, d = self.get_json("/api/overflows/SBB00407")
        self.assertEqual((status, d["id"]), (200, "SBB00407"))
        self.assertEqual(self.request("/api/overflows/MISSING")[0], 404)


class Bathing(Server):
    def test_samples_only_for_known_bathing_waters(self):
        self.con.execute("""INSERT INTO bathing_waters (id, name, lat, lon, updated)
                            VALUES ('33300', 'Polzeath', 50.57, -4.92, 0)""")
        self.con.commit()
        self.assertEqual(self.request("/api/bathing/99999")[0], 404)
        self.assertEqual(self.request("/api/bathing/ukk3104-33300")[0], 404)
        with mock.patch.object(bathing, "samples", return_value={"samples": []}):
            self.assertEqual(self.get_json("/api/bathing/33300"), (200, {"samples": []}))
        with mock.patch.object(bathing, "samples", side_effect=OSError("down")):
            self.assertEqual(self.request("/api/bathing/33300")[0], 502)


class Marine(Server):
    def test_marine_history(self):
        self.con.execute("INSERT INTO marine_stations (id, name, source, lat, lon, updated) "
                         "VALUES ('62107', 'Seven Stones lightvessel', 'ndbc', 50.102, -6.1, 0)")
        self.con.execute("INSERT INTO marine_obs (station, ts, wvht) VALUES ('62107', ?, 1.9)", (int(time.time()) - 600,))
        self.con.commit()
        status, d = self.get_json("/api/marine/62107?days=2")
        self.assertEqual((status, d["days"], len(d["readings"])), (200, 2, 1))
        self.assertEqual(self.request("/api/marine/99999")[0], 404)
        self.assertEqual(self.get_json("/api/marine/62107?days=x")[1]["days"], 7)


class Forecasts(Server):
    def test_rain_forecast_only_for_rain_gauges(self):
        self.add_station("LEVEL")
        self.add_station("RAIN", kind="rain", measure="RAIN-rainfall", unit="mm", typical=None)
        self.assertEqual(self.request("/api/forecast/rain/LEVEL")[0], 404)
        with mock.patch.object(forecast, "rain", return_value={"hours": []}) as rain:
            self.assertEqual(self.get_json("/api/forecast/rain/RAIN"), (200, {"hours": []}))
        rain.assert_called_once_with(50.4, -4.9)

    def test_flow_forecast_only_for_known_outlets(self):
        self.assertEqual(self.request("/api/forecast/flow/12345")[0], 404)
        with mock.patch.object(forecast, "flow", return_value={"days": []}):
            self.assertEqual(self.get_json("/api/forecast/flow/47117"), (200, {"days": []}))

    def test_an_upstream_failure_is_a_502(self):
        with mock.patch.object(forecast, "flow", side_effect=OSError("down")):
            self.assertEqual(self.request("/api/forecast/flow/47117")[0], 502)


if __name__ == "__main__":
    unittest.main()
