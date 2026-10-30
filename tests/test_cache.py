"""The on-demand cache shared by forecasts and other popup data."""

import unittest
import urllib.error
from unittest import mock

from monitor import cache
from tests.support import QuietTestCase


class Cache(QuietTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_a_fresh_answer_is_reused(self):
        fetch = mock.Mock(return_value=1)
        self.assertEqual(cache.cached("k", 60, fetch), 1)
        self.assertEqual(cache.cached("k", 60, fetch), 1)
        self.assertEqual(fetch.call_count, 1)

    def test_the_last_good_answer_survives_a_failure(self):
        cache.cached("k", 0, lambda: "good")
        failing = mock.Mock(side_effect=urllib.error.URLError("down"))
        self.assertEqual(cache.cached("k", 0, failing), "good")
        failing.assert_called_once()

    def test_a_failure_with_nothing_cached_raises(self):
        with self.assertRaises(urllib.error.URLError):
            cache.cached("k", 60, mock.Mock(side_effect=urllib.error.URLError("down")))

    def test_keys_are_separate(self):
        self.assertEqual(cache.cached("a", 60, lambda: 1), 1)
        self.assertEqual(cache.cached("b", 60, lambda: 2), 2)


if __name__ == "__main__":
    unittest.main()
