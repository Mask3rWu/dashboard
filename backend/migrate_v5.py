"""v4 → v5 schema migration.

Adds scale_factor column to column_registry for per-column display scaling.
"""

import logging

logger = logging.getLogger(__name__)


def run_migration(conn):
    """Execute v4→v5 migration inside a transaction.

    Steps:
    1. ALTER TABLE column_registry ADD COLUMN scale_factor REAL DEFAULT 1.0
    2. Record schema_version = 5
    """
    logger.info("Starting v4→v5 migration (column scale_factor)...")
    conn.execute("PRAGMA foreign_keys=OFF")

    # Step 1: Add scale_factor column
    conn.execute(
        "ALTER TABLE column_registry ADD COLUMN scale_factor REAL DEFAULT 1.0"
    )
    logger.info("  Added column_registry.scale_factor REAL DEFAULT 1.0")

    # Step 2: Record migration
    conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (5)")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    logger.info("Migration v4→v5 complete!")
