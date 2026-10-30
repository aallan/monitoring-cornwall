"""SQLite storage for monitoring stations and their readings."""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "monitor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    id            TEXT PRIMARY KEY,      -- Environment Agency station reference
    kind          TEXT NOT NULL,         -- 'level' or 'rain'
    measure       TEXT NOT NULL UNIQUE,  -- EA measure notation for the series we keep
    name          TEXT NOT NULL,
    river         TEXT,
    lat           REAL NOT NULL,
    lon           REAL NOT NULL,
    unit          TEXT,
    typical_low   REAL,                  -- river levels only: EA typical range
    typical_high  REAL,
    max_on_record REAL,
    active        INTEGER NOT NULL DEFAULT 1,
    updated       INTEGER NOT NULL       -- Unix seconds
);

CREATE TABLE IF NOT EXISTS readings (
    measure TEXT    NOT NULL,
    ts      INTEGER NOT NULL,            -- Unix seconds, UTC
    value   REAL    NOT NULL,
    PRIMARY KEY (measure, ts)
) WITHOUT ROWID;

-- Storm overflows: South West Water's Event Duration Monitors
CREATE TABLE IF NOT EXISTS overflows (
    id           TEXT PRIMARY KEY,       -- permit ID, e.g. SBB00407
    name         TEXT,                   -- from the annual returns, where listed
    asset_type   TEXT,
    water        TEXT,                   -- receiving watercourse
    lat          REAL NOT NULL,
    lon          REAL NOT NULL,
    status       INTEGER,                -- 1 spilling, 0 not spilling, -1 monitor offline
    status_start INTEGER,                -- Unix seconds
    last_seen    INTEGER,                -- the feed's lastUpdated for this overflow
    active       INTEGER NOT NULL DEFAULT 1,
    updated      INTEGER NOT NULL
);

-- One row per spill, built up by polling: the feed holds only the latest.
CREATE TABLE IF NOT EXISTS overflow_events (
    id      TEXT    NOT NULL,
    started INTEGER NOT NULL,            -- Unix seconds
    ended   INTEGER,                     -- NULL while still spilling
    PRIMARY KEY (id, started)
) WITHOUT ROWID;

-- Yearly totals from the regulatory annual returns
CREATE TABLE IF NOT EXISTS overflow_annual (
    id     TEXT    NOT NULL,
    year   INTEGER NOT NULL,
    hours  REAL,
    spills INTEGER,
    PRIMARY KEY (id, year)
) WITHOUT ROWID;

-- Environment Agency flood warning and alert areas that cover Cornwall.
-- Polygons are fetched only once an area has had a warning, then kept.
CREATE TABLE IF NOT EXISTS flood_areas (
    id          TEXT PRIMARY KEY,        -- EA flood area notation
    kind        TEXT NOT NULL,           -- 'warning' or 'alert'
    label       TEXT,
    description TEXT,
    river       TEXT,
    county      TEXT,
    geometry    TEXT,                    -- simplified GeoJSON geometry, once fetched
    active      INTEGER NOT NULL DEFAULT 1,
    updated     INTEGER NOT NULL
);

-- Environment Agency designated bathing waters, keyed by sampling point.
CREATE TABLE IF NOT EXISTS bathing_waters (
    id             TEXT PRIMARY KEY,     -- sampling point notation, e.g. 33300
    eubwid         TEXT,                 -- bathing water notation, e.g. ukk3104-33300
    name           TEXT NOT NULL,
    lat            REAL NOT NULL,
    lon            REAL NOT NULL,
    classification TEXT,                 -- Excellent, Good, Sufficient or Poor
    class_year     INTEGER,
    risk           TEXT,                 -- the day's pollution risk forecast: normal or increased
    risk_expires   INTEGER,              -- Unix seconds; forecasts run May to September
    sample_ts      INTEGER,              -- the latest sample
    ec             REAL,                 -- E. coli, cfu/100 ml
    ec_q           TEXT,                 -- '<' or '>' where the count is a limit
    ie             REAL,                 -- intestinal enterococci, cfu/100 ml
    ie_q           TEXT,
    rain_affected  INTEGER,              -- water quality can drop after heavy rain
    active         INTEGER NOT NULL DEFAULT 1,
    updated        INTEGER NOT NULL
);

-- Hourly observations from offshore stations; NULL where not reported.
CREATE TABLE IF NOT EXISTS marine_obs (
    station TEXT    NOT NULL,
    ts      INTEGER NOT NULL,            -- Unix seconds, UTC
    wdir    REAL,                        -- wind direction, degrees from
    wspd    REAL,                        -- wind speed, m/s
    gst     REAL,                        -- gust, m/s
    wvht    REAL,                        -- significant wave height, m
    dpd     REAL,                        -- dominant wave period, s
    apd     REAL,                        -- average wave period, s
    pres    REAL,                        -- air pressure, hPa
    atmp    REAL,                        -- air temperature, °C
    wtmp    REAL,                        -- sea temperature, °C
    PRIMARY KEY (station, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(read_only=False):
    """Open the database, creating it and its schema if needed."""
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    if read_only:
        con.execute("PRAGMA query_only=ON")
    return con


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))
