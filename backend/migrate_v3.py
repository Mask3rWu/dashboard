"""v2 → v3 schema migration.

Migrates:
  - Relax format_category CHECK constraint (A/B/C → any non-empty string)
  - Add config_path column
  - Copy format config files to per-model configs
"""

import os
import sys
import shutil
import logging

logger = logging.getLogger(__name__)


def _get_config_dir():
    """Resolve config directory (inline to avoid depending on format_configs)."""
    if getattr(sys, 'frozen', False):
        from backend.database import DATA_DIR
        return os.path.join(DATA_DIR, 'configs')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'configs')


def run_migration(conn):
    """Execute v2→v3 migration inside a transaction.

    Steps:
    1. Create aircraft_models_new table with relaxed schema + config_path
    2. Copy data, create per-model config files
    3. Swap tables
    4. Record schema_version = 3
    """
    logger.info("Starting v2→v3 migration...")
    conn.execute("PRAGMA foreign_keys=OFF")

    config_dir = _get_config_dir()

    # Step 1: Create new table with relaxed CHECK and config_path column
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aircraft_models_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            format_category TEXT NOT NULL CHECK(format_category != ''),
            config_path     TEXT DEFAULT '',
            description     TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # Step 2: Copy data and create per-model config files
    rows = conn.execute("SELECT * FROM aircraft_models").fetchall()
    for row in rows:
        model_id = row['id']
        fmt = row['format_category']
        description = row['description'] if 'description' in row.keys() else ''
        created_at = row['created_at'] if 'created_at' in row.keys() else None

        # Copy format config to per-model config
        src = os.path.join(config_dir, f"format_{fmt}.json")
        dst_name = f"model_{model_id}.json"
        dst = os.path.join(config_dir, dst_name)
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
                logger.info(f"  Copied config: {src} → {dst}")
            except Exception as e:
                logger.warning(f"  Failed to copy config {src}: {e}")
                dst_name = ''
        elif os.path.exists(dst):
            logger.info(f"  Config already exists: {dst}")

        conn.execute(
            "INSERT INTO aircraft_models_new (id, name, format_category, config_path, description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (model_id, row['name'], fmt, dst_name, description, created_at)
        )

    # Step 3: Swap tables
    conn.execute("DROP TABLE aircraft_models")
    conn.execute("ALTER TABLE aircraft_models_new RENAME TO aircraft_models")

    # Step 4: Record migration
    conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (3)")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    logger.info("Migration v2→v3 complete!")
