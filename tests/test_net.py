"""Requests with a time limit on the whole answer."""

import gzip
import unittest
from unittest import mock

from monitor import net


class FakeResponse:
    """A response that hands out its body a piece at a time."""

    def __init__(self, pieces, headers=None):
        self.pieces = list(pieces)
        self.headers = headers or {}

    def read1(self, n):
        return self.pieces.pop(0) if self.pieces else b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Fetch(unittest.TestCase):
    def test_reads_the_whole_body(self):
        with mock.patch.object(net.urllib.request, "urlopen", return_value=FakeResponse([b"ab", b"cd", b"e"])):
            self.assertEqual(net.fetch("http://x"), b"abcde")

    def test_decompresses_gzip(self):
        body = gzip.compress(b'{"ok": true}')
        resp = FakeResponse([body[:10], body[10:]], {"Content-Encoding": "gzip"})
        with mock.patch.object(net.urllib.request, "urlopen", return_value=resp):
            self.assertEqual(net.fetch("http://x"), b'{"ok": true}')

    def test_a_trickling_answer_gives_up_at_the_deadline(self):
        # A server sending a byte at a time, never finishing; each read takes
        # ten seconds of the clock.
        clock = iter(range(0, 10_000, 10))
        resp = FakeResponse([b"x"] * 1000)
        with mock.patch.object(net.urllib.request, "urlopen", return_value=resp), \
                mock.patch.object(net.time, "monotonic", side_effect=lambda: next(clock)):
            with self.assertRaises(TimeoutError):
                net.fetch("http://x", total=60)
        self.assertGreater(len(resp.pieces), 900)   # it stopped early, not at the end

    def test_passes_the_wait_timeout_to_urlopen(self):
        with mock.patch.object(net.urllib.request, "urlopen", return_value=FakeResponse([b""])) as urlopen:
            net.fetch("http://x", timeout=7)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)


if __name__ == "__main__":
    unittest.main()
