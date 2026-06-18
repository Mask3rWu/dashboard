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
    format_category TEXT NOT NULL CHECK(format_category != ''),
    config_path     TEXT DEFAULT '',
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
    scale_factor    REAL DEFAULT 1.0,
    UNIQUE(model_id, table_name, column_name)
);
CREATE INDEX IF NOT EXISTS idx_colreg_model ON column_registry(model_id, data_type_key);

-- Presets (scoped per model)
CREATE TABLE IF NOT EXISTS presets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL REFERENCES aircraft_models(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    columns_json    TEXT NOT NULL,
    UNIQUE(model_id, name)
);

-- Filter presets (scoped per model)
CREATE TABLE IF NOT EXISTS filter_presets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL REFERENCES aircraft_models(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    config_json     TEXT NOT NULL,
    UNIQUE(model_id, name)
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


def _is_migrated_v3(conn):
    """Check if v3 migration has been applied."""
    rows = conn.execute(
        "SELECT version FROM schema_version WHERE version >= 3"
    ).fetchall()
    return len(rows) > 0


def _run_v2_migration(conn):
    """Run migration from v1 to v2 schema.

    This is imported from backend.migrate_v2 at runtime to avoid circular imports.
    """
    from backend.migrate_v2 import run_migration
    run_migration(conn)


def _run_v3_migration(conn):
    """Run migration from v2 to v3 schema (relax format_category, add config_path).

    This is imported from backend.migrate_v3 at runtime to avoid circular imports.
    """
    from backend.migrate_v3 import run_migration
    run_migration(conn)


def _is_migrated_v4(conn):
    """Check if v4 migration has been applied."""
    rows = conn.execute(
        "SELECT version FROM schema_version WHERE version >= 4"
    ).fetchall()
    return len(rows) > 0


def _run_v4_migration(conn):
    """Run migration from v3 to v4 schema (preset → model scoping).

    This is imported from backend.migrate_v4 at runtime to avoid circular imports.
    """
    from backend.migrate_v4 import run_migration
    run_migration(conn)


def _is_migrated_v5(conn):
    """Check if v5 migration has been applied."""
    rows = conn.execute(
        "SELECT version FROM schema_version WHERE version >= 5"
    ).fetchall()
    return len(rows) > 0


def _run_v5_migration(conn):
    """Run migration from v4 to v5 schema (column scale_factor).

    This is imported from backend.migrate_v5 at runtime to avoid circular imports.
    """
    from backend.migrate_v5 import run_migration
    run_migration(conn)


def _is_migrated_v6(conn):
    """Check if v6 migration has been applied."""
    rows = conn.execute(
        "SELECT version FROM schema_version WHERE version >= 6"
    ).fetchall()
    return len(rows) > 0


def _run_v6_migration(conn):
    """Run migration from v5 to v6 schema (full datetime in flights).

    This is imported from backend.migrate_v6 at runtime to avoid circular imports.
    """
    from backend.migrate_v6 import run_migration
    run_migration(conn)


def init_db():
    """Create all management tables. Run migrations if needed.

    Data tables (model_N_*_data) are created dynamically when models
    are registered, not during init.
    """
    # Ensure persistent config files exist (copied from _MEIPASS in frozen mode)
    from backend.format_configs import _ensure_persistent_configs
    _ensure_persistent_configs()

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

    # v3 migration: relax format_category, add config_path
    if not _is_migrated_v3(conn):
        logger.info("v2 schema detected — running migration to v3")
        try:
            _run_v3_migration(conn)
        except Exception as e:
            logger.error(f"v3 migration failed: {e}")
            raise

    # v4 migration: model-scoped presets
    if not _is_migrated_v4(conn):
        logger.info("v3 schema detected — running migration to v4")
        try:
            _run_v4_migration(conn)
        except Exception as e:
            logger.error(f"v4 migration failed: {e}")
            raise

    # v5 migration: column scale_factor
    if not _is_migrated_v5(conn):
        logger.info("v4 schema detected — running migration to v5")
        try:
            _run_v5_migration(conn)
        except Exception as e:
            logger.error(f"v5 migration failed: {e}")
            raise

    # v6 migration: full datetime in flights
    if not _is_migrated_v6(conn):
        logger.info("v5 schema detected — running migration to v6")
        try:
            _run_v6_migration(conn)
        except Exception as e:
            logger.error(f"v6 migration failed: {e}")
            raise

    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    conn.close()
