"""v3 → v4 schema migration.

Migrates:
  - Add model_id to presets table (with UNIQUE(model_id, name))
  - Add model_id to filter_presets table (with UNIQUE(model_id, name))
  - Assign all existing presets to the first aircraft model
"""

import logging

logger = logging.getLogger(__name__)


def run_migration(conn):
    """Execute v3→v4 migration inside a transaction.

    Steps:
    1. Determine default model_id (MIN id from aircraft_models)
    2. Recreate presets table with model_id + UNIQUE(model_id, name)
    3. Recreate filter_presets table with model_id + UNIQUE(model_id, name)
    4. Record schema_version = 4
    """
    logger.info("Starting v3→v4 migration (preset → model scoping)...")
    conn.execute("PRAGMA foreign_keys=OFF")

    # Step 1: Get default model_id
    default = conn.execute(
        "SELECT MIN(id) as mid FROM aircraft_models"
    ).fetchone()
    default_model_id = default['mid'] if default and default['mid'] else None

    if default_model_id is None:
        logger.warning("  No aircraft models found — presets will not be migrated")
    else:
        logger.info(f"  Using default model_id={default_model_id} for existing presets")

    # Step 2: Recreate presets table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS presets_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id        INTEGER NOT NULL REFERENCES aircraft_models(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            columns_json    TEXT NOT NULL,
            UNIQUE(model_id, name)
        )
    """)

    if default_model_id is not None:
        conn.execute(
            "INSERT INTO presets_new (id, model_id, name, columns_json) "
            "SELECT id, ?, name, columns_json FROM presets",
            (default_model_id,)
        )
        count = conn.execute("SELECT COUNT(*) as cnt FROM presets").fetchone()['cnt']
        logger.info(f"  Migrated {count} presets to model_id={default_model_id}")

    conn.execute("DROP TABLE presets")
    conn.execute("ALTER TABLE presets_new RENAME TO presets")
    logger.info("  Swapped presets table")

    # Step 3: Recreate filter_presets table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filter_presets_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id        INTEGER NOT NULL REFERENCES aircraft_models(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            config_json     TEXT NOT NULL,
            UNIQUE(model_id, name)
        )
    """)

    if default_model_id is not None:
        conn.execute(
            "INSERT INTO filter_presets_new (id, model_id, name, config_json) "
            "SELECT id, ?, name, config_json FROM filter_presets",
            (default_model_id,)
        )
        count = conn.execute("SELECT COUNT(*) as cnt FROM filter_presets").fetchone()['cnt']
        logger.info(f"  Migrated {count} filter_presets to model_id={default_model_id}")

    conn.execute("DROP TABLE filter_presets")
    conn.execute("ALTER TABLE filter_presets_new RENAME TO filter_presets")
    logger.info("  Swapped filter_presets table")

    # Step 4: Record migration
    conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (4)")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    logger.info("Migration v3→v4 complete!")
