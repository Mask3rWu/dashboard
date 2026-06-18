"""v5 → v6 schema migration.

Updates flights.start_time and flights.end_time from bare HH:MM:SS
to full YYYY-MM-DD HH:MM:SS by concatenating with flight_date.
"""

import logging

logger = logging.getLogger(__name__)


def run_migration(conn):
    """Execute v5→v6 migration inside a transaction.

    Steps:
    1. Concatenate flight_date with start_time for rows that have
       a flight_date and whose start_time does not already contain
       a date prefix (test: no '-' in the first 10 chars).
    2. Same for end_time.
    3. Record schema_version = 6
    """
    logger.info("Starting v5→v6 migration (full datetime in flights)...")
    conn.execute("PRAGMA foreign_keys=OFF")

    # Step 1: Update start_time
    updated_start = conn.execute(
        """UPDATE flights
           SET start_time = flight_date || ' ' || start_time
           WHERE flight_date IS NOT NULL
             AND start_time IS NOT NULL
             AND start_time NOT LIKE '____-__-__ %'"""
    ).rowcount
    logger.info(f"  Updated {updated_start} start_time values")

    # Step 2: Update end_time
    updated_end = conn.execute(
        """UPDATE flights
           SET end_time = flight_date || ' ' || end_time
           WHERE flight_date IS NOT NULL
             AND end_time IS NOT NULL
             AND end_time NOT LIKE '____-__-__ %'"""
    ).rowcount
    logger.info(f"  Updated {updated_end} end_time values")

    # Step 3: Record migration
    conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (6)")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    logger.info("Migration v5→v6 complete!")
