#!/usr/bin/env python3
"""Local server for Monitoring Cornwall.

Serves the static site in site/. Standard library only.

    python3 serve.py              # http://localhost:8090
    python3 serve.py 9000         # custom port
    python3 serve.py --bind ::    # listen on all interfaces (deployment)
"""

import argparse
import socket
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITE = Path(__file__).resolve().parent / "site"
DEFAULT_PORT = 8090

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
    args = ap.parse_args()

    host = "127.0.0.1" if args.bind == "localhost" else args.bind
    if ":" in host:
        Server.address_family = socket.AF_INET6

    httpd = Server((host, args.port), partial(Handler, directory=str(SITE)))
    print(f"Monitoring Cornwall on http://localhost:{args.port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
