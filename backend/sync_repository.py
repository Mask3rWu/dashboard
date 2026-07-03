"""Repository helpers for offline sync import/export metadata."""

from __future__ import annotations

import json


def get_setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def selected_ids(conn, flight_ids: list[int]) -> dict[str, set[int]]:
    if not flight_ids:
        raise ValueError("至少选择一个架次")
    placeholders = ",".join("?" for _ in flight_ids)
    rows = conn.execute(
        f"""SELECT f.id as flight_id, a.id as aircraft_id, am.id as model_id
            FROM flights f
            JOIN aircraft a ON a.id = f.aircraft_id
            JOIN aircraft_models am ON am.id = a.model_id
            WHERE f.id IN ({placeholders})""",
        flight_ids,
    ).fetchall()
    found_flights = {r["flight_id"] for r in rows}
    missing = sorted(set(flight_ids) - found_flights)
    if missing:
        raise ValueError(f"架次不存在: {missing}")
    return {
        "flights": found_flights,
        "aircraft": {r["aircraft_id"] for r in rows},
        "models": {r["model_id"] for r in rows},
    }


def rows_by_ids(conn, quote_identifier, table: str, ids: set[int]) -> list[dict]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM {quote_identifier(table)} WHERE id IN ({placeholders}) ORDER BY id",
        sorted(ids),
    ).fetchall()
    return [dict(r) for r in rows]


def list_existing_models(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name FROM aircraft_models ORDER BY name, id"
    ).fetchall()
    return [dict(r) for r in rows]


def list_aircraft_for_model(conn, model_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name FROM aircraft WHERE model_id=? ORDER BY name, id",
        (model_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def insert_sync_import_report(
    conn,
    package_path: str,
    source_node_id: str | None,
    status: str,
    report: dict,
) -> int:
    conn.execute(
        """INSERT INTO sync_imports (package_path, source_node_id, status, report_json)
           VALUES (?, ?, ?, ?)""",
        (
            package_path,
            source_node_id,
            status,
            json.dumps(report, ensure_ascii=False),
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def get_sync_import_report(conn, import_id: int):
    return conn.execute(
        "SELECT * FROM sync_imports WHERE id=?",
        (import_id,),
    ).fetchone()
