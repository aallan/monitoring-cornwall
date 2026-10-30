#!/usr/bin/env python3
"""Server for Monitoring Cornwall.

Serves the site in site/, answers /api/ requests for station history, and
polls the Environment Agency every 15 minutes in a background thread.
Standard library only.

    python3 serve.py              # http://localhost:8090
    python3 serve.py 9000         # custom port
    python3 serve.py --bind ::    # listen on all interfaces (deployment)
    python3 serve.py --no-poll    # serve only; something else runs the poller
"""

import argparse
import json
import re
import socket
import time
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from monitor import db, poll

SITE = Path(__file__).resolve().parent / "site"
DEFAULT_PORT = 8090
MAX_HISTORY_DAYS = 28
STATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")

# Basemap tiles are the only third-party origin the page may load.
TILE_HOST = "https://server.arcgisonline.com"

CSP = (
    "default-src 'self'; "
    f"img-src 'self' data: {TILE_HOST}; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".geojson": "application/geo+json",
        ".js": "text/javascript",
        ".svg": "image/svg+xml",
    }

    def end_headers(self):
        # Revalidate everything (cheap 304s via Last-Modified), so a changed
        # script or overlay is never served stale.
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path.startswith("/api/"):
            self.handle_api(url)
        else:
            super().do_GET()

    def do_HEAD(self):
        if urllib.parse.urlsplit(self.path).path.startswith("/api/"):
            self.send_error(405)
        else:
            super().do_HEAD()

    def send_json(self, obj, status=200):
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_api(self, url):
        # /api/stations/<id>/readings?days=N
        parts = url.path.strip("/").split("/")
        if len(parts) != 4 or parts[:2] != ["api", "stations"] or parts[3] != "readings" \
                or not STATION_ID.match(parts[2]):
            return self.send_json({"error": "not found"}, 404)
        try:
            days = int(urllib.parse.parse_qs(url.query).get("days", ["7"])[0])
        except ValueError:
            days = 7
        days = max(1, min(MAX_HISTORY_DAYS, days))
        con = db.connect(read_only=True)
        try:
            s = con.execute("SELECT * FROM stations WHERE id = ?", (parts[2],)).fetchone()
            if s is None:
                return self.send_json({"error": "no such station"}, 404)
            rows = con.execute("SELECT ts, value FROM readings WHERE measure = ? AND ts > ? ORDER BY ts",
                               (s["measure"], int(time.time()) - days * 86400)).fetchall()
        finally:
            con.close()
        self.send_json({
            "id": s["id"], "kind": s["kind"], "name": s["name"], "unit": s["unit"],
            "typical": [s["typical_low"], s["typical_high"]] if s["kind"] == "level" else None,
            "days": days,
            "readings": [[r["ts"], r["value"]] for r in rows],
        })

    def list_directory(self, path):
        self.send_error(404)
        return None

    def log_message(self, fmt, *args):
        # Quieter than the default: skip successful static hits.
        if len(args) > 1 and str(args[1]).startswith(("2", "3")):
            return
        super().log_message(fmt, *args)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            # Accept IPv4 too when bound to an IPv6 address.
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT)
    ap.add_argument("--bind", default="localhost", help="address to bind (default: localhost)")
    ap.add_argument("--no-poll", action="store_true", help="don't poll the Environment Agency")
    args = ap.parse_args()

    host = "127.0.0.1" if args.bind == "localhost" else args.bind
    if ":" in host:
        Server.address_family = socket.AF_INET6

    httpd = Server((host, args.port), partial(Handler, directory=str(SITE)))
    if not args.no_poll:
        poll.start_thread()
    print(f"Monitoring Cornwall on http://localhost:{args.port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
