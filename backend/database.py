"""Database initialization, connection management, and schema validation.

SQLite remains the only implemented backend for the local desktop/offline
build. The ``DB_BACKEND`` switch is intentionally explicit so repository code
has a stable connection boundary when the research-network deployment later
moves to MySQL or another server database.
"""

import os
import sys
import sqlite3
import logging
import shutil
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

def _default_data_dir():
    if sys.platform == 'win32':
        return os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'FlightAnalyzer')
    return os.path.join(os.path.expanduser('~'), '.flightanalyzer')


# Data directory: DATA_DIR env override, otherwise %APPDATA%/FlightAnalyzer on
# Windows and ~/.flightanalyzer elsewhere. The value is fixed for one process.
DATA_DIR = os.path.abspath(os.path.expanduser(os.environ.get('DATA_DIR') or _default_data_dir()))

DB_BACKEND = os.environ.get("DB_BACKEND", "sqlite").strip().lower() or "sqlite"
DB_PATH = os.path.join(DATA_DIR, 'data.db')
# Current medium-term schema starts from v2 and rebuilds incompatible old DBs.
CURRENT_SCHEMA_VERSION = 2
CORE_TABLES = {
    'schema_version', 'aircraft_models', 'aircraft', 'flights',
    'data_table_registry', 'column_registry', 'presets', 'filter_presets',
    'app_settings', 'users', 'auth_sessions',
    'file_objects', 'flight_raw_files', 'sync_imports', 'sync_runs',
}
SYNC_COLUMNS = {
    'client_uid', 'server_id', 'source_node_id', 'sync_origin', 'sync_state',
    'server_version', 'last_sync_at', 'sync_error_json', 'updated_at',
    'deleted_at', 'server_deleted_at',
}
REQUIRED_COLUMNS = {
    'aircraft_models': {
        'id', 'name', 'has_header', 'has_uav_send_id',
        'extract_serial_from_path', 'created_at',
    } | SYNC_COLUMNS,
    'aircraft': {
        'id', 'model_id', 'name', 'created_at',
    } | SYNC_COLUMNS,
    'flights': {
        'id', 'aircraft_id', 'name', 'source_path', 'session_key',
        'flight_date', 'start_time', 'end_time', 'duration_sec',
        'total_rows', 'import_time', 'record_daily_duration_min',
        'record_batch_name', 'record_location', 'record_payload',
        'record_weather', 'record_fuel_amount', 'record_takeoff_weight',
        'record_altitude', 'record_wind_speed', 'record_note',
        'raw_import_warnings',
    } | SYNC_COLUMNS,
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
    } | SYNC_COLUMNS,
    'flight_raw_files': {
        'id', 'flight_id', 'file_object_id', 'original_name',
        'original_rel_path', 'data_type_key', 'source_mtime', 'created_at',
    } | SYNC_COLUMNS,
    'sync_imports': {
        'id', 'package_path', 'source_node_id', 'status', 'report_json',
        'created_at',
    } | SYNC_COLUMNS,
    'sync_runs': {
        'id', 'run_type', 'status', 'started_at', 'finished_at',
        'summary_json', 'error_json',
    },
}

def ensure_data_dir():
    """Create the application data directory with contextual errors."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        for name in ('objects', 'sync_exports', 'sync_cache', 'manifests'):
            os.makedirs(os.path.join(DATA_DIR, name), exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"Cannot create data directory: {DATA_DIR}. "
            f"Check folder permissions and disk space. Original error: {e}"
        ) from e


def _require_sqlite_backend():
    if DB_BACKEND != "sqlite":
        raise RuntimeError(
            f"DB_BACKEND={DB_BACKEND!r} is configured, but only 'sqlite' is "
            "implemented in this build. Use the repository layer as the "
            "replacement boundary for future server database support."
        )


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
    _seed_runtime_settings(conn)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _upsert_setting(conn, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO app_settings (key, value, updated_at)
           VALUES (?, ?, datetime('now','localtime'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value),
    )


def _get_setting(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _seed_runtime_settings(conn) -> None:
    """Seed local runtime settings used by sync triggers and context APIs."""
    local_node_id = _get_setting(conn, 'local_node_id') or _get_setting(conn, 'node_id')
    if not local_node_id:
        local_node_id = f"node-{uuid.uuid4().hex[:8]}"
    _upsert_setting(conn, 'local_node_id', local_node_id)

    # Keep legacy app/context consumers working while new sync code uses
    # local_node_id.
    if not _get_setting(conn, 'node_id'):
        _upsert_setting(conn, 'node_id', local_node_id)

    if os.environ.get('SERVER_BASE_URL') is not None or _get_setting(conn, 'server_base_url') is None:
        _upsert_setting(conn, 'server_base_url', os.environ.get('SERVER_BASE_URL', '').strip())
    if os.environ.get('SYNC_ENABLED') is not None or _get_setting(conn, 'sync_enabled') is None:
        _upsert_setting(conn, 'sync_enabled', 'true' if _env_bool('SYNC_ENABLED', True) else 'false')


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
    client_uid      TEXT,
    server_id       INTEGER,
    source_node_id  TEXT,
    sync_origin     TEXT NOT NULL DEFAULT 'local' CHECK(sync_origin IN ('local', 'server', 'package')),
    sync_state      TEXT NOT NULL DEFAULT 'pending_upload',
    server_version  INTEGER,
    last_sync_at    TEXT,
    sync_error_json TEXT,
    name            TEXT NOT NULL UNIQUE,
    has_header      INTEGER DEFAULT 1,
    has_uav_send_id INTEGER DEFAULT 0,
    extract_serial_from_path INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime')),
    deleted_at      TEXT,
    server_deleted_at TEXT
);

-- Individual aircraft (飞机) — `name` is a free-form aircraft label
CREATE TABLE IF NOT EXISTS aircraft (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_uid      TEXT,
    server_id       INTEGER,
    source_node_id  TEXT,
    sync_origin     TEXT NOT NULL DEFAULT 'local' CHECK(sync_origin IN ('local', 'server', 'package')),
    sync_state      TEXT NOT NULL DEFAULT 'pending_upload',
    server_version  INTEGER,
    last_sync_at    TEXT,
    sync_error_json TEXT,
    model_id        INTEGER NOT NULL REFERENCES aircraft_models(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime')),
    deleted_at      TEXT,
    server_deleted_at TEXT,
    UNIQUE(model_id, name)
);

-- Flight sessions (飞行架次)
CREATE TABLE IF NOT EXISTS flights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_uid      TEXT,
    server_id       INTEGER,
    source_node_id  TEXT,
    sync_origin     TEXT NOT NULL DEFAULT 'local' CHECK(sync_origin IN ('local', 'server', 'package')),
    sync_state      TEXT NOT NULL DEFAULT 'pending_upload',
    server_version  INTEGER,
    last_sync_at    TEXT,
    sync_error_json TEXT,
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
    updated_at      TEXT DEFAULT (datetime('now','localtime')),
    deleted_at      TEXT,
    server_deleted_at TEXT,
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
    client_uid       TEXT,
    server_id        INTEGER,
    source_node_id   TEXT,
    sync_origin      TEXT NOT NULL DEFAULT 'local' CHECK(sync_origin IN ('local', 'server', 'package')),
    sync_state       TEXT NOT NULL DEFAULT 'pending_upload',
    server_version   INTEGER,
    last_sync_at     TEXT,
    sync_error_json  TEXT,
    sha256           TEXT NOT NULL UNIQUE,
    size_bytes       INTEGER NOT NULL,
    storage_rel_path TEXT NOT NULL,
    created_at       TEXT DEFAULT (datetime('now','localtime')),
    updated_at       TEXT DEFAULT (datetime('now','localtime')),
    deleted_at       TEXT,
    server_deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS flight_raw_files (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    client_uid        TEXT,
    server_id         INTEGER,
    source_node_id    TEXT,
    sync_origin       TEXT NOT NULL DEFAULT 'local' CHECK(sync_origin IN ('local', 'server', 'package')),
    sync_state        TEXT NOT NULL DEFAULT 'pending_upload',
    server_version    INTEGER,
    last_sync_at      TEXT,
    sync_error_json   TEXT,
    flight_id         INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    file_object_id    INTEGER NOT NULL REFERENCES file_objects(id),
    original_name     TEXT NOT NULL,
    original_rel_path TEXT NOT NULL,
    data_type_key     TEXT,
    source_mtime      REAL,
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    updated_at        TEXT DEFAULT (datetime('now','localtime')),
    deleted_at        TEXT,
    server_deleted_at TEXT,
    UNIQUE(flight_id, file_object_id, original_rel_path)
);
CREATE INDEX IF NOT EXISTS idx_flight_raw_files_flight ON flight_raw_files(flight_id);

-- Offline sync import reports for research-network package ingestion.
CREATE TABLE IF NOT EXISTS sync_imports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    client_uid     TEXT,
    server_id      INTEGER,
    source_node_id TEXT,
    sync_origin    TEXT NOT NULL DEFAULT 'package' CHECK(sync_origin IN ('local', 'server', 'package')),
    sync_state     TEXT NOT NULL DEFAULT 'local_only',
    server_version INTEGER,
    last_sync_at   TEXT,
    sync_error_json TEXT,
    package_path   TEXT NOT NULL,
    status         TEXT NOT NULL,
    report_json    TEXT NOT NULL,
    created_at     TEXT DEFAULT (datetime('now','localtime')),
    updated_at     TEXT DEFAULT (datetime('now','localtime')),
    deleted_at     TEXT,
    server_deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type     TEXT NOT NULL CHECK(run_type IN ('push', 'pull', 'full')),
    status       TEXT NOT NULL CHECK(status IN ('running', 'success', 'failed')),
    started_at   TEXT DEFAULT (datetime('now','localtime')),
    finished_at  TEXT,
    summary_json TEXT,
    error_json   TEXT
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_aircraft_models_client_uid ON aircraft_models(client_uid) WHERE client_uid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_aircraft_client_uid ON aircraft(client_uid) WHERE client_uid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_flights_client_uid ON flights(client_uid) WHERE client_uid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_file_objects_client_uid ON file_objects(client_uid) WHERE client_uid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_flight_raw_files_client_uid ON flight_raw_files(client_uid) WHERE client_uid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_imports_client_uid ON sync_imports(client_uid) WHERE client_uid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_flights_sync_state ON flights(sync_state);

CREATE TRIGGER IF NOT EXISTS trg_aircraft_models_sync_insert
AFTER INSERT ON aircraft_models
WHEN NEW.client_uid IS NULL OR NEW.source_node_id IS NULL
BEGIN
    UPDATE aircraft_models
       SET client_uid = COALESCE(NEW.client_uid, lower(hex(randomblob(16)))),
           source_node_id = COALESCE(NEW.source_node_id, (SELECT value FROM app_settings WHERE key='local_node_id'), 'node-' || lower(hex(randomblob(4))))
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_aircraft_sync_insert
AFTER INSERT ON aircraft
WHEN NEW.client_uid IS NULL OR NEW.source_node_id IS NULL
BEGIN
    UPDATE aircraft
       SET client_uid = COALESCE(NEW.client_uid, lower(hex(randomblob(16)))),
           source_node_id = COALESCE(NEW.source_node_id, (SELECT value FROM app_settings WHERE key='local_node_id'), 'node-' || lower(hex(randomblob(4))))
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_flights_sync_insert
AFTER INSERT ON flights
WHEN NEW.client_uid IS NULL OR NEW.source_node_id IS NULL
BEGIN
    UPDATE flights
       SET client_uid = COALESCE(NEW.client_uid, lower(hex(randomblob(16)))),
           source_node_id = COALESCE(NEW.source_node_id, (SELECT value FROM app_settings WHERE key='local_node_id'), 'node-' || lower(hex(randomblob(4))))
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_file_objects_sync_insert
AFTER INSERT ON file_objects
WHEN NEW.client_uid IS NULL OR NEW.source_node_id IS NULL
BEGIN
    UPDATE file_objects
       SET client_uid = COALESCE(NEW.client_uid, lower(hex(randomblob(16)))),
           source_node_id = COALESCE(NEW.source_node_id, (SELECT value FROM app_settings WHERE key='local_node_id'), 'node-' || lower(hex(randomblob(4))))
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_flight_raw_files_sync_insert
AFTER INSERT ON flight_raw_files
WHEN NEW.client_uid IS NULL OR NEW.source_node_id IS NULL
BEGIN
    UPDATE flight_raw_files
       SET client_uid = COALESCE(NEW.client_uid, lower(hex(randomblob(16)))),
           source_node_id = COALESCE(NEW.source_node_id, (SELECT value FROM app_settings WHERE key='local_node_id'), 'node-' || lower(hex(randomblob(4))))
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_sync_imports_sync_insert
AFTER INSERT ON sync_imports
WHEN NEW.client_uid IS NULL
BEGIN
    UPDATE sync_imports
       SET client_uid = COALESCE(NEW.client_uid, lower(hex(randomblob(16))))
     WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_aircraft_models_updated_at
AFTER UPDATE ON aircraft_models
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE aircraft_models SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_aircraft_updated_at
AFTER UPDATE ON aircraft
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE aircraft SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_flights_updated_at
AFTER UPDATE ON flights
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE flights SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_file_objects_updated_at
AFTER UPDATE ON file_objects
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE file_objects SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_flight_raw_files_updated_at
AFTER UPDATE ON flight_raw_files
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE flight_raw_files SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_sync_imports_updated_at
AFTER UPDATE ON sync_imports
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE sync_imports SET updated_at = datetime('now','localtime') WHERE id = NEW.id;
END;
"""


def get_db():
    """Get a database connection with row factory."""
    _require_sqlite_backend()
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
    _require_sqlite_backend()
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
            _seed_runtime_settings(conn)
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
