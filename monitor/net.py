"""HTTP requests with a time limit on the whole answer.

urllib's timeout covers connecting and each wait for more data, not the
whole response, so a server that keeps a connection open, or sends a little
at a time, can hold a request indefinitely. That would stall the poller, which
fetches its feeds one after another. Every request the poller and the server
make goes through fetch(), which gives up once the answer has taken longer
than TOTAL_TIMEOUT.
"""

import gzip
import time
import urllib.request

WAIT_TIMEOUT = 30     # seconds: to connect, and for each wait for more data
TOTAL_TIMEOUT = 120   # seconds: for the whole answer
CHUNK = 64 * 1024


def fetch(req, timeout=WAIT_TIMEOUT, total=TOTAL_TIMEOUT):
    """The body of a response, decompressed if it was gzipped. Raises
    TimeoutError if the whole answer takes longer than `total` seconds."""
    deadline = time.monotonic() + total
    with urllib.request.urlopen(req, timeout=timeout) as r:
        chunks = []
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"no complete answer within {total} s")
            chunk = r.read1(CHUNK)
            if not chunk:
                break
            chunks.append(chunk)
        body = b"".join(chunks)
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return body
