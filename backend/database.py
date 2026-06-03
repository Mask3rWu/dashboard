"""SQLite database initialization and connection management."""

import os
import sys
import sqlite3

# Data directory: %APPDATA%/FlightAnalyzer on Windows, ~/.flightanalyzer elsewhere
if sys.platform == 'win32':
    DATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'FlightAnalyzer')
else:
    DATA_DIR = os.path.join(os.path.expanduser('~'), '.flightanalyzer')

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'data.db')

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS flights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    drone_id        TEXT NOT NULL,
    drone_model     TEXT DEFAULT 'CR500A',
    source_path     TEXT NOT NULL,
    session_key     TEXT NOT NULL DEFAULT '',
    flight_date     TEXT,
    start_time      TEXT,
    end_time        TEXT,
    duration_sec    REAL,
    total_rows      INTEGER DEFAULT 0,
    import_time     TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(source_path, drone_id, session_key)
);

CREATE TABLE IF NOT EXISTS presets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    columns_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filter_presets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    config_json     TEXT NOT NULL
);

-- GPS Data (1Hz, dual-antenna)
CREATE TABLE IF NOT EXISTS gps_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    time_str        TEXT NOT NULL,
    time_sec        REAL NOT NULL,
    north_vel       REAL,
    east_vel        REAL,
    down_vel        REAL,
    nava_lat        REAL,
    nava_lng        REAL,
    nava_alt        REAL,
    gpsb_vel_n      REAL,
    gpsb_vel_e      REAL,
    gpsb_vel_d      REAL,
    navb_lat        REAL,
    navb_lng        REAL,
    navb_alt        REAL,
    pos_accuracy    REAL,
    vel_accuracy    REAL,
    pressure        REAL,
    pressure_alt    REAL,
    heading         REAL,
    baseline_len    REAL,
    update_freq     REAL
);
CREATE INDEX IF NOT EXISTS idx_gps_ft ON gps_data(flight_id, time_sec);

-- IMU Data (1Hz, dual Nav system)
CREATE TABLE IF NOT EXISTS imu_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    time_str        TEXT NOT NULL,
    time_sec        REAL NOT NULL,
    nava_roll       REAL,
    nava_pitch      REAL,
    nava_yaw        REAL,
    vx              REAL,
    vy              REAL,
    vz              REAL,
    ax              REAL,
    ay              REAL,
    az              REAL,
    navb_roll       REAL,
    navb_pitch      REAL,
    navb_yaw        REAL,
    navb_vx         REAL,
    navb_vy         REAL,
    navb_vz         REAL,
    navb_ax         REAL,
    navb_ay         REAL,
    navb_az         REAL,
    ax_extremum     REAL,
    ay_extremum     REAL,
    az_extremum     REAL
);
CREATE INDEX IF NOT EXISTS idx_imu_ft ON imu_data(flight_id, time_sec);

-- Drone State Data (~7Hz)
CREATE TABLE IF NOT EXISTS drone_state_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    time_str        TEXT NOT NULL,
    time_sec        REAL NOT NULL,
    uplink_type     TEXT,
    downlink_type   TEXT,
    roll            REAL,
    pitch           REAL,
    yaw             REAL,
    fwd_vel         REAL,
    side_vel        REAL,
    down_vel        REAL,
    fwd_target_vel  REAL,
    side_target_vel REAL,
    down_target_vel REAL,
    target_yaw      REAL,
    target_pitch    REAL,
    yaw_rate        REAL,
    target_yaw_rate REAL,
    target_alt      REAL,
    battery_pct     REAL,
    servo_battery   REAL,
    link_quality    REAL,
    link_switches   INTEGER,
    flight_mode     TEXT,
    flight_time     REAL,
    remaining_time  REAL
);
CREATE INDEX IF NOT EXISTS idx_ds_ft ON drone_state_data(flight_id, time_sec);

-- Position Data (~7Hz)
CREATE TABLE IF NOT EXISTS pos_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    time_str        TEXT NOT NULL,
    time_sec        REAL NOT NULL,
    north_pos       REAL,
    east_pos        REAL,
    rel_alt         REAL,
    lng             REAL,
    lat             REAL,
    alt_amsl        REAL,
    home_dist       REAL,
    fcs_voltage     REAL,
    is_nava_online  INTEGER,
    is_navb_online  INTEGER,
    is_gcs_online   INTEGER,
    is_powerbox_online INTEGER,
    is_ecu_online   INTEGER,
    is_tcu_online   INTEGER,
    is_servo1_online INTEGER,
    is_servo2_online INTEGER,
    is_servo3_online INTEGER,
    is_servo4_online INTEGER,
    is_servo5_online INTEGER,
    is_servo6_online INTEGER,
    flight_mode     TEXT,
    recorder_state  TEXT,
    target_route    INTEGER,
    cross_track_err REAL,
    navia_state     TEXT,
    navib_state     TEXT,
    gps_time_h      INTEGER,
    gps_time_m      INTEGER,
    gps_time_s      INTEGER,
    pdop            REAL,
    prewarn_pos     INTEGER,
    prewarn_boundary INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pos_ft ON pos_data(flight_id, time_sec);

-- Engine Data (1Hz)
CREATE TABLE IF NOT EXISTS engine_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    time_str        TEXT NOT NULL,
    time_sec        REAL NOT NULL,
    cyl_head_temp   REAL,
    exhaust_temp_1  REAL,
    exhaust_temp_2  REAL,
    engine_temp     REAL,
    intake_temp_1   REAL,
    intake_temp_2   REAL,
    intake_temp_3   REAL,
    intake_temp_4   REAL,
    rpm             REAL,
    throttle        REAL,
    manifold_press  REAL,
    fuel_remaining  REAL,
    battery_voltage REAL,
    tcu_temp        REAL,
    tcu_manifold    REAL
);
CREATE INDEX IF NOT EXISTS idx_engine_ft ON engine_data(flight_id, time_sec);

-- PowerBox Data (1Hz)
CREATE TABLE IF NOT EXISTS powerbox_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    time_str        TEXT NOT NULL,
    time_sec        REAL NOT NULL,
    fcs_voltage     REAL,
    servo_voltage   REAL,
    rcvr_voltage    REAL,
    battery_voltage REAL,
    fcs_current     REAL,
    servo_current   REAL,
    rcvr_current    REAL,
    battery_current REAL,
    v12             REAL,
    v28             REAL,
    servo_current_alt REAL
);
CREATE INDEX IF NOT EXISTS idx_powerbox_ft ON powerbox_data(flight_id, time_sec);

-- Dual Antenna Diff Nav (1Hz)
CREATE TABLE IF NOT EXISTS dual_antenna_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    time_str        TEXT NOT NULL,
    time_sec        REAL NOT NULL,
    gps_pos_type    TEXT,
    radio_pos_flag  TEXT,
    pa_alt_flag     TEXT,
    mag_heading_flag TEXT,
    gps_data_flag   TEXT,
    pdop_diff       REAL,
    hdop_diff       REAL,
    sat_num_diff    INTEGER,
    pos_update_rate REAL,
    vel_update_rate REAL,
    state_update_rate REAL,
    pressure        REAL,
    pressure_2      REAL,
    vel             REAL,
    pressure_rate   REAL,
    pressure2_rate  REAL,
    pressure_temp   REAL,
    pressure2_temp  REAL,
    pressure_alt_sensor TEXT,
    pressure_alt_comp   TEXT
);
CREATE INDEX IF NOT EXISTS idx_da_ft ON dual_antenna_data(flight_id, time_sec);

-- Flight Alerts
CREATE TABLE IF NOT EXISTS flight_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id       INTEGER NOT NULL REFERENCES flights(id) ON DELETE CASCADE,
    time_str        TEXT NOT NULL,
    time_sec        REAL NOT NULL,
    uav_id          TEXT,
    drone_model     TEXT,
    alert_desc      TEXT,
    extra_value     TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_ft ON flight_alerts(flight_id, time_sec);
"""


def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate_schema(conn):
    """Idempotent migrations for upgrading schema in-place.

    Adds session_key column and upgrades UNIQUE constraint from
    (source_path, drone_id) to (source_path, drone_id, session_key).
    Uses table rebuild. FK constraints are preserved because
    init_db() creates all tables with PRAGMA foreign_keys=OFF.
    """
    conn.row_factory = sqlite3.Row

    # 1. Add session_key column if missing
    cols = [r['name'] for r in conn.execute("PRAGMA table_info(flights)").fetchall()]
    if 'session_key' not in cols:
        conn.execute("ALTER TABLE flights ADD COLUMN session_key TEXT NOT NULL DEFAULT ''")
        conn.commit()

    # 2. Check if old 2-column UNIQUE is still active
    needs_rebuild = False
    indexes = conn.execute("PRAGMA index_list(flights)").fetchall()
    for idx in indexes:
        if idx['origin'] == 'u':
            idx_info = conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()
            idx_cols = sorted([r['name'] for r in idx_info])
            if idx_cols == ['drone_id', 'source_path']:
                needs_rebuild = True
                break

    if needs_rebuild:
        # Detect which columns exist in old table for safe migration
        old_cols = set(r['name'] for r in conn.execute("PRAGMA table_info(flights)").fetchall())
        DATA_TABLES = [
            'gps_data', 'imu_data', 'drone_state_data', 'pos_data',
            'engine_data', 'powerbox_data', 'dual_antenna_data', 'flight_alerts',
        ]

        def _col(name, default='NULL'):
            return name if name in old_cols else default

        # 1. Build new flights table
        conn.execute("""
            CREATE TABLE flights_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                drone_id        TEXT NOT NULL,
                drone_model     TEXT DEFAULT 'CR500A',
                source_path     TEXT NOT NULL,
                session_key     TEXT NOT NULL DEFAULT '',
                flight_date     TEXT,
                start_time      TEXT,
                end_time        TEXT,
                duration_sec    REAL,
                total_rows      INTEGER DEFAULT 0,
                import_time     TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(source_path, drone_id, session_key)
            )
        """)
        select_parts = [
            'id', 'name', 'drone_id',
            _col('drone_model', "COALESCE((SELECT drone_model FROM flights LIMIT 1), 'CR500A')"),
            'source_path',
            "''",
            _col('flight_date'),
            _col('start_time'),
            _col('end_time'),
            _col('duration_sec'),
            _col('total_rows', '0'),
            _col('import_time', "datetime('now','localtime')"),
        ]
        conn.execute(f"INSERT INTO flights_new SELECT {', '.join(select_parts)} FROM flights")
        conn.execute("DROP TABLE flights")
        conn.execute("ALTER TABLE flights_new RENAME TO flights")

        # 2. Recreate each child data table to fix FK references
        for tbl in DATA_TABLES:
            # Check if table exists
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
            if not exists:
                continue
            # Get original CREATE SQL, replace to point to flights_new (now renamed to flights)
            old_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()[0]
            new_sql = old_sql.replace(tbl, tbl + '_new')
            conn.execute(new_sql)
            conn.execute(f"INSERT INTO {tbl}_new SELECT * FROM {tbl}")
            conn.execute(f"DROP TABLE {tbl}")
            conn.execute(f"ALTER TABLE {tbl}_new RENAME TO {tbl}")
            # Recreate time index
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{tbl}_ft ON {tbl}(flight_id, time_sec)"
            )

        conn.commit()


def init_db():
    """Create all tables. Idempotent."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA)
    _migrate_schema(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    conn.close()
