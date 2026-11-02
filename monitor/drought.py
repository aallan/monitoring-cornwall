"""Drought status in Cornwall, from the Environment Agency's weekly report.

The Environment Agency publishes "Dry weather and drought in England" each
week on GOV.UK, under the Open Government Licence. Its third section lists
the Environment Agency Areas at each stage of drought response (prolonged
dry weather, drought, severe drought, recovery), and its second the water
companies with a temporary use ban, the hosepipe ban, in force. Cornwall is
part of the Devon, Cornwall and Isles of Scilly Area, which can be at two
stages at once: parts of it supplied by particular reservoirs in severe
drought and the rest in drought.

GOV.UK's search and content APIs serve the reports as JSON. The reports for
each year are a separate series, so the latest series is found by search
rather than by its address, which has the year in it. The poller keeps the
entries that name Cornwall or the Isles of Scilly, word for word, and South
West Water's ban.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from . import ea, net

GOVUK = "https://www.gov.uk"
SEARCH_URL = GOVUK + "/api/search.json"
CONTENT_URL = GOVUK + "/api/content"
SERIES_TITLE = re.compile(r"^Dry weather and drought in England \d{4}: summary reports$")
REPORT_DATE = re.compile(r"(\d{1,2}) (January|February|March|April|May|June|July|August|September|"
                         r"October|November|December) (\d{4})$")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")

# Section IDs, as GOV.UK generates them from the headings, and each stage's
# name, most severe first.
STAGE_PREFIX = "environment-agency-areas-in-"
STAGES = {"severe-drought": "Severe drought", "drought": "Drought",
          "prolonged-dry-weather": "Prolonged dry weather", "recovery": "Recovery"}
BANS_PREFIX = "active-temporary-use-bans"
AREAS = ("Cornwall", "Isles of Scilly")
COMPANY = "South West Water"

REFRESH = 6 * 3600   # the report is weekly
ROOT = Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "site" / "data" / "drought.json"


def get(url, params=None):
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(url + query, headers={"User-Agent": ea.USER_AGENT})
    return json.loads(net.fetch(req))


class Sections(HTMLParser):
    """The list items of a GOV.UK page, by the ID of the heading they sit
    under, and any paragraph that isn't in a list item: a stage with one area
    is sometimes a paragraph rather than a list."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.section, self.items, self.buf, self.depth = None, {}, None, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("h2", "h3"):
            self.section = dict(attrs).get("id")
        elif tag == "li" or (tag == "p" and self.buf is None):
            if self.buf is None:
                self.buf, self.depth = [], 0
            if tag == "li":
                self.depth += 1

    def handle_endtag(self, tag):
        if self.buf is None:
            return
        if tag == "li":
            self.depth -= 1
        if (tag == "li" and self.depth == 0) or (tag == "p" and self.depth == 0):
            text = " ".join("".join(self.buf).split())
            if text and self.section:
                self.items.setdefault(self.section, []).append(text)
            self.buf = None

    def handle_data(self, data):
        if self.buf is not None:
            self.buf.append(data)


def parse(body):
    """{"stages": [{"stage", "text"}], "bans": [text]} for Cornwall and the
    Isles of Scilly. A report with no stage sections at all has changed shape,
    and raises rather than reporting no drought."""
    p = Sections()
    p.feed(body)
    found = {k[len(STAGE_PREFIX):]: v for k, v in p.items.items() if k.startswith(STAGE_PREFIX)}
    if not any(slug in found for slug in STAGES):
        raise ValueError("no drought stages in the report")
    stages = [{"stage": name, "text": t} for slug, name in STAGES.items()
              for t in found.get(slug, []) if any(a in t for a in AREAS)]
    bans = [t for k, v in p.items.items() if k.startswith(BANS_PREFIX)
            for t in v if t.startswith(COMPANY)]
    return {"stages": stages, "bans": bans}


def report_date(title):
    """The last day a report covers, from its title: '… 23 to 29 October 2026'."""
    m = REPORT_DATE.search(title or "")
    return date(int(m.group(3)), MONTHS.index(m.group(2)) + 1, int(m.group(1))) if m else None


def latest_series(results):
    for r in results:
        if SERIES_TITLE.match(r.get("title", "")) and r.get("link", "").startswith("/government/publications/"):
            return r["link"]
    raise ValueError("no drought report series found")


def latest_report(series):
    reports = [a for a in series.get("details", {}).get("attachments", [])
               if a.get("attachment_type") == "html" and str(a.get("url", "")).startswith("/government/")]
    if not reports:
        raise ValueError("no reports in the series")
    dated = [a for a in reports if report_date(a.get("title"))]
    return max(dated, key=lambda a: report_date(a["title"])) if dated else reports[0]


def fetch():
    search = get(SEARCH_URL, {"q": '"dry weather and drought in england"',
                              "filter_organisations": "environment-agency",
                              "order": "-public_timestamp", "count": 10, "fields": "title,link,public_timestamp"})
    series = get(CONTENT_URL + latest_series(search.get("results", [])))
    report = latest_report(series)
    content = get(CONTENT_URL + report["url"])
    status = parse(content["details"]["body"])
    title = content.get("title") or report["title"]
    return {"report": {"title": title, "url": GOVUK + report["url"],
                       "period": title.split(": ", 1)[-1], "published": content.get("public_updated_at")},
            **status}


def write_summary(status, now=None):
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"generated": int(now or time.time()), **status},
                              separators=(",", ":"), ensure_ascii=False))
    tmp.replace(SUMMARY)


def poll(now=None):
    status = fetch()
    write_summary(status, now)
    return status
