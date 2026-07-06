"""Repository helpers for offline sync import/export metadata."""

from __future__ import annotations

import json


UPLOAD_QUEUE_STATES = ("pending_upload", "dirty", "upload_failed")


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


def list_upload_queue(conn, states: tuple[str, ...] = UPLOAD_QUEUE_STATES) -> list[dict]:
    """Return local flights that can be packed into a push bundle."""
    if not states:
        return []
    placeholders = ",".join("?" for _ in states)
    rows = conn.execute(
        f"""SELECT f.id, f.client_uid, f.server_id, f.source_node_id, f.sync_origin,
                  f.sync_state, f.server_version, f.last_sync_at, f.sync_error_json,
                  f.name, f.session_key, f.flight_date, f.start_time, f.duration_sec,
                  f.total_rows, f.import_time, f.updated_at,
                  f.record_batch_name, f.record_location, f.record_weather,
                  f.record_payload,
                  a.id as aircraft_id, a.name as aircraft_name,
                  am.id as model_id, am.name as model_name,
                  COALESCE(rf.raw_file_count, 0) as raw_file_count
           FROM flights f
           JOIN aircraft a ON a.id = f.aircraft_id
           JOIN aircraft_models am ON am.id = a.model_id
           LEFT JOIN (
               SELECT flight_id, COUNT(*) as raw_file_count
               FROM flight_raw_files
               GROUP BY flight_id
           ) rf ON rf.flight_id = f.id
           WHERE f.sync_state IN ({placeholders})
             AND f.deleted_at IS NULL
             AND f.server_deleted_at IS NULL
           ORDER BY
             CASE f.sync_state
               WHEN 'conflict' THEN 0
               WHEN 'upload_failed' THEN 1
               WHEN 'dirty' THEN 2
               WHEN 'pending_upload' THEN 3
               ELSE 9
             END,
             f.updated_at DESC,
             f.id DESC""",
        states,
    ).fetchall()
    return [dict(r) for r in rows]


def upload_queue_summary(conn) -> dict:
    rows = conn.execute(
        """SELECT sync_state, COUNT(*) as count
           FROM flights
           WHERE sync_state IN ('pending_upload', 'dirty', 'upload_failed', 'conflict')
             AND deleted_at IS NULL
             AND server_deleted_at IS NULL
           GROUP BY sync_state"""
    ).fetchall()
    counts = {row["sync_state"]: int(row["count"] or 0) for row in rows}
    return {
        "pending_upload": counts.get("pending_upload", 0),
        "dirty": counts.get("dirty", 0),
        "upload_failed": counts.get("upload_failed", 0),
        "conflict": counts.get("conflict", 0),
        "uploadable": sum(counts.get(state, 0) for state in UPLOAD_QUEUE_STATES),
    }


def validate_uploadable_flights(conn, flight_ids: list[int]) -> list[dict]:
    """Return selected queue rows, raising if any selected flight cannot upload."""
    clean_ids = sorted({int(fid) for fid in flight_ids})
    if not clean_ids:
        raise ValueError("至少选择一个待上传架次")
    placeholders = ",".join("?" for _ in clean_ids)
    rows = conn.execute(
        f"""SELECT id, name, sync_state
            FROM flights
            WHERE id IN ({placeholders})
            ORDER BY id""",
        clean_ids,
    ).fetchall()
    found = {int(row["id"]): dict(row) for row in rows}
    missing = sorted(set(clean_ids) - set(found))
    if missing:
        raise ValueError(f"架次不存在: {missing}")
    invalid = [
        {"id": fid, "sync_state": found[fid]["sync_state"]}
        for fid in clean_ids
        if found[fid]["sync_state"] not in UPLOAD_QUEUE_STATES
    ]
    if invalid:
        raise ValueError(f"架次不在上传队列中: {invalid}")
    return [found[fid] for fid in clean_ids]


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
