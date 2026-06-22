"""v6 → v7 schema migration.

Migrates model configuration from JSON files into the database,
eliminating the dual-storage (DB + JSON) pattern.

Steps:
  1. Add model-level config columns to aircraft_models
  2. Add per-data-type config columns to data_table_registry
  3. Read existing per-model JSON configs, populate DB columns
  4. Clear config_path (mark migrated)
  5. Record schema_version = 7
"""

import os
import json
import logging

logger = logging.getLogger(__name__)


def _get_config_dir():
    """Resolve config directory (same logic as format_configs.py before removal)."""
    import sys
    if getattr(sys, 'frozen', False):
        from backend.database import DATA_DIR
        return os.path.join(DATA_DIR, 'configs')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'configs')


def run_migration(conn):
    """Execute v6→v7 migration inside a transaction."""
    logger.info("Starting v6→v7 migration (JSON config → DB)...")
    conn.execute("PRAGMA foreign_keys=OFF")

    # ── Step 1: Add model-level config columns to aircraft_models ──
    model_cols = [
        ("has_header", "INTEGER DEFAULT 1"),
        ("has_uav_send_id", "INTEGER DEFAULT 0"),
        ("encoding", "TEXT DEFAULT 'utf-8'"),
        ("extract_serial_from_path", "INTEGER DEFAULT 0"),
        ("has_aircraft_prefix", "INTEGER DEFAULT 0"),
    ]
    for col_name, col_def in model_cols:
        _add_column_if_missing(conn, "aircraft_models", col_name, col_def)

    # ── Step 2: Add per-data-type config columns to data_table_registry ──
    dt_cols = [
        ("file_patterns", "TEXT DEFAULT '[]'"),
        ("is_alert", "INTEGER DEFAULT 0"),
    ]
    for col_name, col_def in dt_cols:
        _add_column_if_missing(conn, "data_table_registry", col_name, col_def)

    # ── Step 3: Migrate existing JSON config data into DB ──
    config_dir = _get_config_dir()
    rows = conn.execute(
        "SELECT id, config_path, name FROM aircraft_models"
    ).fetchall()

    for row in rows:
        model_id = row['id']
        config_path = row['config_path']

        if not config_path:
            # Try model_{id}.json as fallback (for re-runs where config_path was cleared)
            fallback_path = os.path.join(config_dir, f"model_{model_id}.json")
            if os.path.exists(fallback_path):
                config_path = f"model_{model_id}.json"
            else:
                logger.info(f"  Model {model_id} ({row['name']}): no config_path and no fallback, skipping")
                continue

        json_path = os.path.join(config_dir, config_path)
        if not os.path.exists(json_path):
            logger.warning(f"  Model {model_id}: config file not found: {json_path}")
            # Clear invalid config_path
            conn.execute(
                "UPDATE aircraft_models SET config_path='' WHERE id=?",
                (model_id,)
            )
            continue

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            logger.warning(f"  Model {model_id}: failed to read {json_path}: {e}")
            continue

        # ── Write model-level settings ──
        conn.execute(
            """UPDATE aircraft_models SET
               has_header = ?,
               has_uav_send_id = ?,
               encoding = ?,
               extract_serial_from_path = ?,
               has_aircraft_prefix = ?,
               config_path = ''
               WHERE id = ?""",
            (
                1 if config.get('has_header') else 0,
                1 if config.get('has_uav_send_id') else 0,
                config.get('encoding', 'utf-8'),
                1 if config.get('extract_serial_from_path') else 0,
                1 if config.get('has_aircraft_prefix') else 0,
                model_id,
            )
        )

        # ── Write per-data-type settings ──
        for dt_key, dt_def in config.get('data_types', {}).items():
            file_patterns_json = json.dumps(
                dt_def.get('file_patterns', []), ensure_ascii=False
            )
            is_alert = 1 if dt_def.get('is_alert') else 0
            conn.execute(
                """UPDATE data_table_registry SET
                   file_patterns = ?,
                   is_alert = ?
                   WHERE model_id = ? AND data_type_key = ?""",
                (file_patterns_json, is_alert, model_id, dt_key)
            )

            # ── Sync columns from JSON to column_registry ──
            # Add any columns that exist in JSON but not yet in the registry
            table_name = f"model_{model_id}_{dt_key}_data"
            existing_cols = conn.execute(
                "SELECT column_name FROM column_registry WHERE model_id=? AND data_type_key=?",
                (model_id, dt_key)
            ).fetchall()
            existing_names = {r['column_name'] for r in existing_cols}

            for col in dt_def.get('columns', []):
                col_name = col.get('name', '')
                if not col_name or col_name in existing_names:
                    continue
                col_type = col.get('type', 'REAL')
                ordinal = col.get('ordinal')
                is_numeric = 1 if col_type.upper() in ('REAL', 'INTEGER', 'FLOAT') else 0
                scale_factor = col.get('scale_factor', 1.0)
                conn.execute(
                    """INSERT OR REPLACE INTO column_registry
                       (model_id, data_type_key, table_name, column_name,
                        display_label, unit, data_type, ordinal, is_numeric, scale_factor)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        model_id, dt_key, table_name, col_name,
                        col.get('label', col_name), col.get('unit', ''),
                        col_type, ordinal, is_numeric, scale_factor,
                    )
                )
                logger.info(f"    Added missing column to registry: {dt_key}.{col_name}")

        logger.info(f"  Model {model_id} ({row['name']}): migrated from {config_path}")

    # ── Step 4: Record migration ──
    conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (7)")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    logger.info("Migration v6→v7 complete!")


def _add_column_if_missing(conn, table, col_name, col_def):
    """Add a column to a table if it doesn't already exist."""
    existing = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing_names = {r['name'] for r in existing}
    if col_name not in existing_names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
        logger.info(f"  Added column: {table}.{col_name} {col_def}")
