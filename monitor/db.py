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
