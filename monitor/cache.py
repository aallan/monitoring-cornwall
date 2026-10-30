"""Fetching JSON for popups on demand, with a small in-memory cache.

Forecasts, bathing water samples and other per-popup data are fetched by the
server when someone asks for them, never by visitors' browsers, and each
answer is kept for a while so repeated popups don't repeat the request.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .ea import USER_AGENT, log

_store = {}
_lock = threading.Lock()


def get_json(url, params=None, timeout=20):
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(url + query, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def cached(key, ttl, fetch):
    """Return a cached value, or fetch and cache it. A failed fetch serves
    the last good value if there is one."""
    now = time.time()
    with _lock:
        hit = _store.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        value = fetch()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as e:
        log(f"request failed for {key}: {e}")
        if hit:
            return hit[1]
        raise
    with _lock:
        _store[key] = (now, value)
    return value


def clear():
    with _lock:
        _store.clear()
