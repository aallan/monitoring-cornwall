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
