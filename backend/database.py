"""SQLite database initialization, connection management, and schema migration."""

import os
import sys
import sqlite3
import logging
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

# Data directory: %APPDATA%/FlightAnalyzer on Windows, ~/.flightanalyzer elsewhere
if sys.platform == 'win32':
    DATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'FlightAnalyzer')
else:
    DATA_DIR = os.path.join(os.path.expanduser('~'), '.flightanalyzer')

DB_PATH = os.path.join(DATA_DIR, 'data.db')
# Current medium-term schema starts from v1 and rebuilds incompatible old DBs.
CURRENT_SCHEMA_VERSION = 1
CORE_TABLES = {
    'schema_version', 'aircraft_models', 'aircraft', 'flights',
    'data_table_registry', 'column_registry', 'presets', 'filter_presets',
    'app_settings', 'users', 'auth_sessions',
    'file_objects', 'flight_raw_files', 'sync_imports',
}
REQUIRED_COLUMNS = {
    'aircraft_models': {
        'id', 'name', 'has_header', 'has_uav_send_id',
        'extract_serial_from_path', 'created_at',
    },
    'aircraft': {
        'id', 'model_id', 'name', 'created_at',
    },
    'flights': {
        'id', 'aircraft_id', 'name', 'source_path', 'session_key',
        'flight_date', 'start_time', 'end_time', 'duration_sec',
        'total_rows', 'import_time', 'record_daily_duration_min',
        'record_batch_name', 'record_location', 'record_payload',
        'record_weather', 'record_fuel_amount', 'record_takeoff_weight',
        'record_altitude', 'record_wind_speed', 'record_note',
        'raw_import_warnings',
    },
    'data_table_registry': {
        'id', 'model_id', 'data_type_key', 'table_name', 'display_label',
        'file_patterns', 'is_alert',
    },
    'column_registry': {
        'id', 'model_id', 'data_type_key', 'table_name', 'column_name',
        'display_label', 'unit', 'data_type', 'ordinal', 'is_numeric',
        'scale_factor',
    },
    'app_settings': {
        'key', 'value', 'updated_at',
    },
    'users': {
        'id', 'username', 'password_hash', 'role', 'created_at',
        'password_changed_at',
    },
    'auth_sessions': {
        'token_hash', 'user_id', 'created_at', 'expires_at',
    },
    'file_objects': {
        'id', 'sha256', 'size_bytes', 'storage_rel_path', 'created_at',
    },
    'flight_raw_files': {
        'id', 'flight_id', 'file_object_id', 'original_name',
        'original_rel_path', 'data_type_key', 'source_mtime', 'created_at',
    },
    'sync_imports': {
        'id', 'package_path', 'source_node_id', 'status', 'report_json',
        'created_at',
    },
}

def ensure_data_dir():
    """Create the application data directory with contextual errors."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"Cannot create data directory: {DATA_DIR}. "
            f"Check folder permissions and disk space. Original error: {e}"
        ) from e


def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {row[0] for row in rows}


def _table_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _max_schema_version(conn):
    row = conn.execute(
        "SELECT MAX(version) FROM schema_version"
    ).fetchone()
    return row[0] if row and row[0] is not None else 0


def _validate_current_schema(conn):
    """Return a reason string when the DB is not compatible with this build."""
    tables = _table_names(conn)
    missing_tables = sorted(CORE_TABLES - tables)
    if missing_tables:
        return f"missing tables: {', '.join(missing_tables)}"

    version = _max_schema_version(conn)
    if version < CURRENT_SCHEMA_VERSION:
        return f"schema version {version} < required {CURRENT_SCHEMA_VERSION}"

    for table_name, required in REQUIRED_COLUMNS.items():
        columns = _table_columns(conn, table_name)
        missing_columns = sorted(required - columns)
        if missing_columns:
            return f"table {table_name} missing columns: {', '.join(missing_columns)}"

    return None


def _backup_existing_database():
    """Move an incompatible DB aside and return the backup path."""
    if not os.path.exists(DB_PATH):
        return None

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{DB_PATH}.backup_{stamp}"
    counter = 1
    while os.path.exists(backup_path):
        backup_path = f"{DB_PATH}.backup_{stamp}_{counter}"
        counter += 1

    suffixes = ['', '-wal', '-shm']
    for suffix in suffixes:
        src = DB_PATH + suffix
        if os.path.exists(src):
            dst = backup_path + suffix
            shutil.move(src, dst)
    return backup_path


def _create_fresh_schema(conn):
    """Create the current management schema and mark it as current."""
    conn.executescript(MANAGEMENT_SCHEMA)
    for v in range(1, CURRENT_SCHEMA_VERSION + 1):
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (v,)
        )


# ── Schema fragments ──

MANAGEMENT_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- Application settings (environment mode and offline node identity)
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

-- Local research-network users
CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    username              TEXT NOT NULL UNIQUE,
    password_hash         TEXT NOT NULL,
    role                  TEXT NOT NULL CHECK(role IN ('admin', 'user')),
    created_at            TEXT DEFAULT (datetime('now','localtime')),
    password_changed_at   TEXT
);

-- Bearer sessions; only token hashes are stored
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash     TEXT PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at     TEXT DEFAULT (datetime('now','localtime')),
    expires_at     TEXT
);

-- Aircraft models (机型)
CREATE TABLE IF NOT EXISTS aircraft_models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    has_header      INTEGER DEFAULT 1,
    has_uav_send_id INTEGER DEFAULT 0,
    extract_serial_from_path INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

-- Individual aircraft (飞机) — `name` is a free-form aircraft label
CREATE TABLE IF NOT EXISTS aircraft (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL REFERENCES aircraft_models(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(model_id, name)
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
    record_daily_duration_min REAL,
    record_batch_name TEXT DEFAULT '',
    record_location TEXT DEFAULT '',
    record_payload TEXT DEFAULT '',
    record_weather TEXT DEFAULT '',
    record_fuel_amount REAL,
    record_takeoff_weight REAL,
    record_altitude REAL,
    record_wind_speed REAL,
    record_note TEXT DEFAULT '',
    raw_import_warnings TEXT DEFAULT '',
    -- Dedup boundary: same aircraft + date + session_key. source_path is
    -- stored for provenance only and intentionally excluded from the unique
    -- constraint (a folder may be moved and re-imported from a new path).
    UNIQUE(aircraft_id, flight_date, session_key)
);

-- Content-addressed raw file object store. Files are physically stored under
-- DATA_DIR/objects/sha256/<prefix>/<sha256>.<ext>, while business ownership is
-- resolved through flight_raw_files.
CREATE TABLE IF NOT EXISTS file_objects (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256           TEXT NOT NULL UNIQUE,
    size_bytes       INTEGER NOT NULL,
    storage_rel_path TEXT NOT NULL,
    created_at       TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS flight_raw_files (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id         INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    file_object_id    INTEGER NOT NULL REFERENCES file_objects(id),
    original_name     TEXT NOT NULL,
    original_rel_path TEXT NOT NULL,
    data_type_key     TEXT,
    source_mtime      REAL,
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(flight_id, file_object_id, original_rel_path)
);
CREATE INDEX IF NOT EXISTS idx_flight_raw_files_flight ON flight_raw_files(flight_id);

-- Offline sync import reports for research-network package ingestion.
CREATE TABLE IF NOT EXISTS sync_imports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    package_path   TEXT NOT NULL,
    source_node_id TEXT,
    status         TEXT NOT NULL,
    report_json    TEXT NOT NULL,
    created_at     TEXT DEFAULT (datetime('now','localtime'))
);

-- Data table registry (maps model × data_type → table_name)
CREATE TABLE IF NOT EXISTS data_table_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL REFERENCES aircraft_models(id) ON DELETE CASCADE,
    data_type_key   TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    display_label   TEXT NOT NULL,
    file_patterns   TEXT DEFAULT '[]',
    is_alert        INTEGER DEFAULT 0,
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


def init_db():
    """Create a current-version database, replacing incompatible old DBs.

    The app is still iterating quickly, so old schemas are not migrated during
    normal startup. Instead, an incompatible database is moved aside as a
    timestamped backup and a clean current schema is created.
    """
    ensure_data_dir()
    db_existed = os.path.exists(DB_PATH)

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=OFF")
    except sqlite3.Error as e:
        raise RuntimeError(f"Cannot open SQLite database: {DB_PATH}. Error: {e}") from e

    try:
        if db_existed:
            reason = _validate_current_schema(conn)
            if reason:
                logger.warning(
                    "Incompatible database detected — backing up and rebuilding: %s",
                    reason,
                )
                conn.close()
                backup_path = _backup_existing_database()
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=OFF")
                _create_fresh_schema(conn)
                conn.execute("PRAGMA foreign_keys=ON")
                conn.commit()
                logger.warning("Old database backed up to: %s", backup_path)
                return {"created": True, "rebuilt": True, "backup_path": backup_path, "reason": reason}

            logger.info("Current database schema verified at %s", DB_PATH)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()
            return {"created": False, "rebuilt": False, "backup_path": None, "reason": None}

        _create_fresh_schema(conn)
        logger.info("Fresh database created at %s", DB_PATH)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        return {"created": True, "rebuilt": False, "backup_path": None, "reason": None}
    except sqlite3.Error as e:
        raise RuntimeError(
            f"Database initialization failed at {DB_PATH}. Error: {e}"
        ) from e
    finally:
        try:
            conn.close()
        except Exception:
            pass
