"""Reset Flight Analyzer test data for local and server environments.

This script clears business/sync data and transferred raw-file storage while
preserving user accounts and runtime configuration. It defaults to dry-run; pass
--yes to actually modify databases and delete storage directories.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import load_app_config  # noqa: E402


LOCAL_STATIC_TABLES = [
    "auth_sessions",
    "filter_presets",
    "presets",
    "sync_runs",
    "sync_imports",
    "flight_raw_files",
    "flights",
    "aircraft",
    "column_registry",
    "data_table_registry",
    "aircraft_models",
]

SERVER_STATIC_TABLES = [
    "auth_sessions",
    "sync_clients",
    "sync_changes",
    "sync_imports",
    "flight_raw_files",
    "flights",
    "aircraft",
    "column_registry",
    "data_table_registry",
    "aircraft_models",
]

LOCAL_STORAGE_DIRS = ["raw_files", "sync_exports", "sync_cache", "manifests", "objects"]
SERVER_STORAGE_DIRS = ["raw_files", "incoming", "bundles", "objects"]


def _sqlite_quote(identifier: str) -> str:
    if "\x00" in identifier:
        raise ValueError(f"Invalid SQLite identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    if not _sqlite_table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) FROM {_sqlite_quote(table)}").fetchone()
    return int(row[0] if row else 0)


def _sqlite_dynamic_tables(conn: sqlite3.Connection) -> list[str]:
    names: set[str] = set()
    if _sqlite_table_exists(conn, "data_table_registry"):
        rows = conn.execute(
            "SELECT table_name FROM data_table_registry WHERE table_name IS NOT NULL"
        ).fetchall()
        names.update(str(row[0]) for row in rows if row[0])
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'model\\_%\\_data' ESCAPE '\\'"
    ).fetchall()
    names.update(str(row[0]) for row in rows if row[0])
    return sorted(names)


def _safe_clear_child_dirs(root: Path, child_names: Iterable[str], *, apply: bool) -> list[Path]:
    root = root.expanduser().resolve()
    cleared: list[Path] = []
    for child_name in child_names:
        target = (root / child_name).resolve()
        if target.parent != root:
            raise RuntimeError(f"Refusing to clear path outside data dir: {target}")
        cleared.append(target)
        if not apply:
            continue
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.mkdir(parents=True, exist_ok=True)
    return cleared


def reset_local(*, apply: bool) -> None:
    from backend import database

    data_dir = Path(database.DATA_DIR)
    db_path = Path(database.DB_PATH)
    print(f"\n[local] DATA_DIR: {data_dir}")
    print(f"[local] DB_PATH:  {db_path}")

    if not db_path.exists():
        print("[local] SQLite database does not exist; storage directories only.")
        cleared = _safe_clear_child_dirs(data_dir, LOCAL_STORAGE_DIRS, apply=apply)
        for path in cleared:
            print(f"[local] {'cleared' if apply else 'would clear'} dir: {path}")
        if apply:
            init_result = database.init_db()
            conn = database.get_db()
            try:
                conn.execute(
                    """INSERT INTO app_settings (key, value, updated_at)
                       VALUES ('builtin_model_seeds_enabled', 'false', datetime('now','localtime'))
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"""
                )
                dynamic_tables = _sqlite_dynamic_tables(conn)
                conn.execute("PRAGMA foreign_keys=OFF")
                for table in dynamic_tables:
                    conn.execute(f"DROP TABLE IF EXISTS {_sqlite_quote(table)}")
                for table in ("column_registry", "data_table_registry", "aircraft_models"):
                    if _sqlite_table_exists(conn, table):
                        conn.execute(f"DELETE FROM {_sqlite_quote(table)}")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.commit()
            finally:
                conn.close()
            print(f"[local] created empty SQLite schema: {init_result}")
        return

    conn = database.get_db()
    try:
        dynamic_tables = _sqlite_dynamic_tables(conn)
        print(f"[local] dynamic tables: {len(dynamic_tables)}")
        for table in dynamic_tables:
            print(f"[local] {'drop' if apply else 'would drop'} table {table} ({_sqlite_count(conn, table)} rows)")
        for table in LOCAL_STATIC_TABLES:
            print(f"[local] {'clear' if apply else 'would clear'} table {table} ({_sqlite_count(conn, table)} rows)")

        cleared = _safe_clear_child_dirs(data_dir, LOCAL_STORAGE_DIRS, apply=False)
        for path in cleared:
            print(f"[local] {'clear' if apply else 'would clear'} dir: {path}")

        if not apply:
            return

        conn.execute("PRAGMA foreign_keys=OFF")
        for table in dynamic_tables:
            conn.execute(f"DROP TABLE IF EXISTS {_sqlite_quote(table)}")
        for table in LOCAL_STATIC_TABLES:
            if _sqlite_table_exists(conn, table):
                conn.execute(f"DELETE FROM {_sqlite_quote(table)}")
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('builtin_model_seeds_enabled', 'false', datetime('now','localtime'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at"""
        )
        if _sqlite_table_exists(conn, "sqlite_sequence"):
            targets = [*LOCAL_STATIC_TABLES]
            placeholders = ",".join("?" for _ in targets)
            conn.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                targets,
            )
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        _safe_clear_child_dirs(data_dir, LOCAL_STORAGE_DIRS, apply=True)
        print("[local] reset complete")
    finally:
        conn.close()


def _server_dynamic_tables(conn, db) -> list[str]:
    names: set[str] = set()
    rows = conn.execute(
        db.text("SELECT table_name FROM data_table_registry WHERE table_name IS NOT NULL")
    ).fetchall()
    names.update(str(row._mapping["table_name"]) for row in rows if row._mapping["table_name"])
    rows = conn.execute(db.text("SHOW TABLES LIKE 'server\\_data\\_m%'")).fetchall()
    for row in rows:
        values = list(row._mapping.values())
        if values and values[0]:
            names.add(str(values[0]))
    return sorted(names)


def _server_table_count(conn, db, table: str) -> int:
    row = conn.execute(
        db.text(f"SELECT COUNT(*) AS count FROM {db.quote_identifier(table)}")
    ).first()
    return int((row._mapping["count"] if row else 0) or 0)


def reset_server(*, apply: bool) -> None:
    from backend import server_database as db

    data_dir = Path(db.SERVER_DATA_DIR)
    print(f"\n[server] SERVER_DATA_DIR: {data_dir}")
    print("[server] MySQL target: configured SERVER_DB_URL (not printed)")

    engine = db.get_engine()
    with engine.begin() as conn:
        db.init_server_schema(engine)
        dynamic_tables = _server_dynamic_tables(conn, db)
        print(f"[server] dynamic tables: {len(dynamic_tables)}")
        for table in dynamic_tables:
            print(f"[server] {'drop' if apply else 'would drop'} table {table} ({_server_table_count(conn, db, table)} rows)")
        for table in SERVER_STATIC_TABLES:
            print(f"[server] {'clear' if apply else 'would clear'} table {table} ({_server_table_count(conn, db, table)} rows)")

        cleared = _safe_clear_child_dirs(data_dir, SERVER_STORAGE_DIRS, apply=False)
        for path in cleared:
            print(f"[server] {'clear' if apply else 'would clear'} dir: {path}")

        if not apply:
            return

        conn.execute(db.text("SET FOREIGN_KEY_CHECKS=0"))
        for table in dynamic_tables:
            conn.execute(db.text(f"DROP TABLE IF EXISTS {db.quote_identifier(table)}"))
        for table in SERVER_STATIC_TABLES:
            conn.execute(db.text(f"DELETE FROM {db.quote_identifier(table)}"))
            try:
                conn.execute(db.text(f"ALTER TABLE {db.quote_identifier(table)} AUTO_INCREMENT=1"))
            except Exception:
                pass
        conn.execute(db.text("SET FOREIGN_KEY_CHECKS=1"))

    _safe_clear_child_dirs(data_dir, SERVER_STORAGE_DIRS, apply=True)
    print("[server] reset complete")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clear local and/or server Flight Analyzer test data."
    )
    parser.add_argument(
        "--scope",
        choices=["local", "server", "all"],
        default="all",
        help="Which environment to reset. Default: all.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional path to flight_analyzer.ini. Defaults to normal config lookup.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually clear data. Without this flag the script only prints a dry-run plan.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = load_app_config(args.config)
    print(f"Config: {config_path or '(none found; environment/defaults used)'}")
    print(f"Mode: {'APPLY' if args.yes else 'DRY-RUN'}")
    print("Preserved: users, server admin account, app_settings, schema_version, flight_analyzer.ini")
    print("Cleared: models, aircraft, flights, parsed dynamic tables, raw-file rows, sync history/cache, transferred raw files")

    if args.scope in {"local", "all"}:
        reset_local(apply=args.yes)
    if args.scope in {"server", "all"}:
        reset_server(apply=args.yes)
    if not args.yes:
        print("\nDry-run only. Re-run with --yes to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
