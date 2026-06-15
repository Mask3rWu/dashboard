"""SQLite database initialization, connection management, and schema migration."""

import os
import sys
import sqlite3
import logging

logger = logging.getLogger(__name__)

# Data directory: %APPDATA%/FlightAnalyzer on Windows, ~/.flightanalyzer elsewhere
if sys.platform == 'win32':
    DATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'FlightAnalyzer')
else:
    DATA_DIR = os.path.join(os.path.expanduser('~'), '.flightanalyzer')

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'data.db')

# ── Schema fragments ──

MANAGEMENT_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- Aircraft models (机型)
CREATE TABLE IF NOT EXISTS aircraft_models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    format_category TEXT NOT NULL CHECK(format_category IN ('A','B','C')),
    description     TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

-- Individual aircraft (飞机序号)
CREATE TABLE IF NOT EXISTS aircraft (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL REFERENCES aircraft_models(id) ON DELETE CASCADE,
    serial_number   TEXT NOT NULL,
    name            TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(model_id, serial_number)
);

-- Flight sessions (飞行架次)
CREATE TABLE IF NOT EXISTS flights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    aircraft_id     INTEGER NOT NULL REFERENCES aircraft(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    source_path     TEXT NOT NULL,
    session_key     TEXT NOT NULL DEFAULT '',
    flight_date     TEXT,
    start_time      TEXT,
    end_time        TEXT,
    duration_sec    REAL,
    total_rows      INTEGER DEFAULT 0,
    import_time     TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(aircraft_id, source_path, session_key)
);

-- Data table registry (maps model × data_type → table_name)
CREATE TABLE IF NOT EXISTS data_table_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL REFERENCES aircraft_models(id) ON DELETE CASCADE,
    data_type_key   TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    display_label   TEXT NOT NULL,
    UNIQUE(model_id, data_type_key)
);

-- Column registry (maps every column for every model)
CREATE TABLE IF NOT EXISTS column_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL REFERENCES aircraft_models(id) ON DELETE CASCADE,
    data_type_key   TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    column_name     TEXT NOT NULL,
    display_label   TEXT NOT NULL,
    unit            TEXT DEFAULT '',
    data_type       TEXT DEFAULT 'REAL',
    ordinal         INTEGER,
    is_numeric      INTEGER DEFAULT 1,
    UNIQUE(model_id, table_name, column_name)
);
CREATE INDEX IF NOT EXISTS idx_colreg_model ON column_registry(model_id, data_type_key);

-- Presets (unchanged from v1)
CREATE TABLE IF NOT EXISTS presets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    columns_json    TEXT NOT NULL
);

-- Filter presets (unchanged from v1)
CREATE TABLE IF NOT EXISTS filter_presets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    config_json     TEXT NOT NULL
);
"""


def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _has_old_schema(conn):
    """Check if the v1 (old) schema is present."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gps_data'"
    ).fetchall()
    return len(rows) > 0


def _is_migrated(conn):
    """Check if v2 migration has been applied."""
    rows = conn.execute(
        "SELECT version FROM schema_version WHERE version >= 2"
    ).fetchall()
    return len(rows) > 0


def _run_v2_migration(conn):
    """Run migration from v1 to v2 schema.

    This is imported from backend.migrate_v2 at runtime to avoid circular imports.
    """
    from backend.migrate_v2 import run_migration
    run_migration(conn)


def init_db():
    """Create all management tables. Run migrations if needed.

    Data tables (model_N_*_data) are created dynamically when models
    are registered, not during init.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")

    # Always create management tables
    conn.executescript(MANAGEMENT_SCHEMA)

    # Check if migration is needed
    if _has_old_schema(conn) and not _is_migrated(conn):
        logger.info("v1 schema detected — running migration to v2")
        try:
            _run_v2_migration(conn)
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise

    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    conn.close()
