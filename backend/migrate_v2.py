"""v1 → v2 schema migration.

Migrates:
  - Old flights table (drone_id, drone_model) → new flights (aircraft_id FK)
  - Old hardcoded data tables → model_1_*_data tables
  - Populates aircraft_models, aircraft, registry tables
"""

import os
import logging
from backend.format_configs import register_model_tables

logger = logging.getLogger(__name__)

OLD_DATA_TABLES = [
    'gps_data', 'imu_data', 'drone_state_data', 'pos_data',
    'engine_data', 'powerbox_data', 'dual_antenna_data', 'flight_alerts',
]

# Map old table names to data_type_keys for Format A
OLD_TABLE_TO_TYPE = {
    'gps_data': 'gps',
    'imu_data': 'imu',
    'drone_state_data': 'drone_state',
    'pos_data': 'pos',
    'engine_data': 'engine',
    'powerbox_data': 'powerbox',
    'dual_antenna_data': 'dual_antenna',
    'flight_alerts': 'alert',
}


def run_migration(conn):
    """Execute the full v1→v2 migration inside a transaction.

    Steps:
    1. Create default Format A model
    2. Create aircraft records for each distinct drone_id
    3. Create model_1_*_data tables via register_model_tables()
    4. Copy old data into new tables
    5. Rebuild flights with aircraft_id FK
    6. Drop old data tables
    7. Record schema_version = 2
    """
    logger.info("Starting v1→v2 migration...")

    # Disable FK checks during migration (MANAGEMENT_SCHEMA turns them ON)
    conn.execute("PRAGMA foreign_keys=OFF")

    # ── Step 1: Create default Format A model ──
    # Use a query to find an existing model, or create one
    model_row = conn.execute("SELECT id FROM aircraft_models WHERE format_category='A' OR name='CR500A'").fetchone()
    if model_row:
        model_id = model_row['id']
        logger.info(f"  Using existing model id={model_id}")
    else:
        conn.execute(
            """INSERT OR IGNORE INTO aircraft_models (name, format_category, description)
               VALUES ('CR500A', 'A', 'Migrated from v1 (Reference Format)')"""
        )
        model_row = conn.execute("SELECT id FROM aircraft_models WHERE format_category='A' OR name='CR500A'").fetchone()
        model_id = model_row['id']
        logger.info(f"  Created model id={model_id} (CR500A, Format A)")

    # ── Step 2: Map old drone_ids to aircraft ──
    drone_ids = conn.execute("SELECT DISTINCT drone_id FROM flights").fetchall()
    aircraft_map = {}  # drone_id → aircraft_id
    for row in drone_ids:
        did = row['drone_id']
        conn.execute(
            "INSERT OR IGNORE INTO aircraft (model_id, serial_number) VALUES (?, ?)",
            (model_id, did)
        )
        ac = conn.execute(
            "SELECT id FROM aircraft WHERE model_id=? AND serial_number=?", (model_id, did)
        ).fetchone()
        aircraft_map[did] = ac['id']
    logger.info(f"  Created {len(aircraft_map)} aircraft records: {list(aircraft_map.keys())}")

    # ── Step 3: Create model_1_*_data tables and populate registry ──
    register_model_tables(conn, model_id, 'A')
    logger.info("  Created model_1_*_data tables and column registry")

    # ── Step 4: Copy data from old tables to new model_1 tables ──
    for old_table in OLD_DATA_TABLES:
        # Check if old table exists
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (old_table,)
        ).fetchone()
        if not exists:
            continue

        dt_key = OLD_TABLE_TO_TYPE.get(old_table, old_table.replace('_data', ''))
        new_table = f"model_{model_id}_{dt_key}_data"

        # Get column names from column_registry for this type
        registry_cols = conn.execute(
            "SELECT column_name FROM column_registry WHERE model_id=? AND data_type_key=? AND ordinal IS NOT NULL ORDER BY ordinal",
            (model_id, dt_key)
        ).fetchall()

        if not registry_cols:
            # No registry entries (e.g., for flight_alerts) — try direct column mapping
            old_cols = [r['name'] for r in conn.execute(f"PRAGMA table_info({old_table})").fetchall()]
            # Filter to common cols: flight_id, time_str, time_sec, and type-specific cols
            # We'll use INSERT INTO ... SELECT for columns that exist in both
            new_cols_info = conn.execute(f"PRAGMA table_info({new_table})").fetchall()
            new_col_names = {r['name'] for r in new_cols_info}
            common_cols = [c for c in old_cols if c in new_col_names and c not in ('id',)]
            if common_cols:
                cols_str = ', '.join(common_cols)
                sql = f"INSERT INTO {new_table} ({cols_str}) SELECT {cols_str} FROM {old_table}"
                conn.execute(sql)
                count = conn.execute(f"SELECT COUNT(*) as cnt FROM {new_table}").fetchone()['cnt']
                logger.info(f"  Copied {count} rows: {old_table} → {new_table}")
        else:
            # Build column mapping — only copy columns that exist in BOTH old and new tables
            reg_names = [r['column_name'] for r in registry_cols]
            old_cols = [r['name'] for r in conn.execute(f"PRAGMA table_info({old_table})").fetchall()]
            new_cols_info = conn.execute(f"PRAGMA table_info({new_table})").fetchall()
            new_col_names = {r['name'] for r in new_cols_info}
            old_col_names = set(old_cols)

            # Common columns: flight_id, time_str, time_sec + registered columns that exist in BOTH
            common = ['flight_id', 'time_str', 'time_sec'] + [
                c for c in reg_names if c in new_col_names and c in old_col_names
            ]
            cols_str = ', '.join(common)

            sql = f"INSERT OR IGNORE INTO {new_table} ({cols_str}) SELECT {cols_str} FROM {old_table}"
            conn.execute(sql)
            count = conn.execute(f"SELECT COUNT(*) as cnt FROM {new_table}").fetchone()['cnt']
            logger.info(f"  Copied {count} rows: {old_table} → {new_table}")

    conn.commit()

    # ── Step 5: Rebuild flights table with aircraft_id ──
    conn.execute("DROP TABLE IF EXISTS flights_new")
    conn.execute("""
        CREATE TABLE flights_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            aircraft_id     INTEGER REFERENCES aircraft(id) ON DELETE CASCADE,
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
        )
    """)

    # Insert with aircraft_id derived from old drone_id
    old_cols = [r['name'] for r in conn.execute("PRAGMA table_info(flights)").fetchall()]
    new_cols = [
        'id', 'name', 'source_path',
        "COALESCE(session_key, '')",
        'flight_date', 'start_time', 'end_time', 'duration_sec',
        'total_rows', 'import_time',
    ]

    # Build CASE expression for aircraft_id lookup
    case_parts = []
    for did, aid in aircraft_map.items():
        case_parts.append(f"WHEN drone_id = '{did}' THEN {aid}")
    if case_parts:
        case_expr = "CASE " + " ".join(case_parts) + " ELSE NULL END"
        select_cols = [case_expr + " as aircraft_id"] + new_cols
        conn.execute(f"INSERT INTO flights_new SELECT {', '.join(select_cols)} FROM flights")
    else:
        conn.execute(f"INSERT INTO flights_new SELECT NULL, {', '.join(new_cols)} FROM flights")

    # Verify no NULL aircraft_id
    null_count = conn.execute("SELECT COUNT(*) as cnt FROM flights_new WHERE aircraft_id IS NULL").fetchone()['cnt']
    if null_count > 0:
        # Assign to first model's first aircraft (or create an "unknown" aircraft)
        first_ac = conn.execute("SELECT id FROM aircraft WHERE model_id=? LIMIT 1", (model_id,)).fetchone()
        if not first_ac:
            conn.execute(
                "INSERT OR IGNORE INTO aircraft (model_id, serial_number, name) VALUES (?, 'unknown', 'Unknown Aircraft')",
                (model_id,)
            )
            first_ac = conn.execute("SELECT id FROM aircraft WHERE model_id=? LIMIT 1", (model_id,)).fetchone()
        conn.execute(
            "UPDATE flights_new SET aircraft_id=? WHERE aircraft_id IS NULL",
            (first_ac['id'],)
        )
        logger.warning(f"  Fixed {null_count} flights with NULL aircraft_id → assigned to 'unknown'")

    conn.execute("DROP TABLE flights")
    conn.execute("ALTER TABLE flights_new RENAME TO flights")
    logger.info("  Rebuilt flights table with aircraft_id FK")

    # ── Step 6: Drop old data tables ──
    for old_table in OLD_DATA_TABLES:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (old_table,)
        ).fetchone()
        if exists:
            conn.execute(f"DROP TABLE {old_table}")
    logger.info("  Dropped old v1 data tables")

    # ── Step 7: Record migration ──
    conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (2)")
    conn.commit()
    logger.info("Migration v1→v2 complete!")


def migrate_standalone():
    """Run migration outside of normal app init (for testing/CLI use)."""
    from backend.database import get_db
    conn = get_db()
    run_migration(conn)
    conn.close()
    print("Migration complete.")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    migrate_standalone()
