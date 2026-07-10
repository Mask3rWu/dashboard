"""Repository helpers for offline sync import/export metadata."""

from __future__ import annotations

import json
from datetime import datetime


UPLOAD_QUEUE_STATES = ("local_only", "pending_upload", "dirty", "upload_failed")
VISIBLE_QUEUE_STATES = ("local_only", "pending_upload", "dirty", "upload_failed", "conflict")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO app_settings (key, value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, str(value), now_text()),
    )


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


def list_upload_queue(conn, states: tuple[str, ...] = VISIBLE_QUEUE_STATES) -> list[dict]:
    """Return local flights that need sync attention."""
    if not states:
        return []
    placeholders = ",".join("?" for _ in states)
    rows = conn.execute(
        f"""SELECT f.id, f.client_uid, f.server_id, f.source_node_id, f.sync_origin,
                  f.sync_state, f.server_version, f.last_sync_at, f.sync_error_json,
                  f.name, f.session_key, f.flight_date, f.start_time, f.duration_sec,
                  f.total_rows, f.import_time, f.updated_at,
                  f.record_location, f.record_weather,
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
               WHEN 'local_only' THEN 3
               ELSE 9
             END,
             f.updated_at DESC,
             f.id DESC""",
        states,
    ).fetchall()
    return [dict(r) for r in rows]


def list_upload_base_queue(conn, states: tuple[str, ...] = VISIBLE_QUEUE_STATES) -> dict[str, list[dict]]:
    """Return local model/aircraft metadata rows that need sync attention."""
    if not states:
        return {"models": [], "aircraft": []}
    placeholders = ",".join("?" for _ in states)
    models = conn.execute(
        f"""SELECT 'model' as entity_type, am.id, am.client_uid, am.server_id,
                  am.source_node_id, am.sync_origin, am.sync_state, am.server_version,
                  am.last_sync_at, am.sync_error_json, am.name, am.updated_at,
                  NULL as model_id, am.name as model_name, NULL as aircraft_name
           FROM aircraft_models am
           WHERE am.sync_state IN ({placeholders})
             AND am.deleted_at IS NULL
             AND am.server_deleted_at IS NULL
           ORDER BY
             CASE am.sync_state
               WHEN 'conflict' THEN 0
               WHEN 'upload_failed' THEN 1
               WHEN 'dirty' THEN 2
               WHEN 'pending_upload' THEN 3
               WHEN 'local_only' THEN 3
               ELSE 9
             END,
             am.updated_at DESC,
             am.id DESC""",
        states,
    ).fetchall()
    aircraft = conn.execute(
        f"""SELECT 'aircraft' as entity_type, a.id, a.client_uid, a.server_id,
                  a.source_node_id, a.sync_origin, a.sync_state, a.server_version,
                  a.last_sync_at, a.sync_error_json, a.name, a.updated_at,
                  am.id as model_id, am.name as model_name, a.name as aircraft_name
           FROM aircraft a
           JOIN aircraft_models am ON am.id = a.model_id
           WHERE a.sync_state IN ({placeholders})
             AND a.deleted_at IS NULL
             AND a.server_deleted_at IS NULL
           ORDER BY
             CASE a.sync_state
               WHEN 'conflict' THEN 0
               WHEN 'upload_failed' THEN 1
               WHEN 'dirty' THEN 2
               WHEN 'pending_upload' THEN 3
               WHEN 'local_only' THEN 3
               ELSE 9
             END,
             am.name,
             a.updated_at DESC,
             a.id DESC""",
        states,
    ).fetchall()
    return {
        "models": [dict(r) for r in models],
        "aircraft": [dict(r) for r in aircraft],
    }


def upload_queue_summary(conn) -> dict:
    rows = []
    for table in ("aircraft_models", "aircraft", "flights"):
        rows.extend(
            conn.execute(
                f"""SELECT sync_state, COUNT(*) as count
                    FROM {table}
                    WHERE sync_state IN ('local_only', 'pending_upload', 'dirty', 'upload_failed', 'conflict')
                      AND deleted_at IS NULL
                      AND server_deleted_at IS NULL
                    GROUP BY sync_state"""
            ).fetchall()
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["sync_state"]] = counts.get(row["sync_state"], 0) + int(row["count"] or 0)
    return {
        "pending_upload": counts.get("local_only", 0) + counts.get("pending_upload", 0),
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
        f"""SELECT f.id, f.client_uid, f.server_id, f.source_node_id, f.sync_origin,
                  f.sync_state, f.server_version, f.last_sync_at, f.sync_error_json,
                  f.name, f.session_key, f.flight_date, f.start_time, f.duration_sec,
                  f.total_rows, f.import_time, f.updated_at,
                  f.record_location, f.record_weather,
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
            WHERE f.id IN ({placeholders})
              AND f.deleted_at IS NULL
              AND f.server_deleted_at IS NULL
            ORDER BY f.id""",
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


def abandon_uploads(conn, flight_ids: list[int]) -> int:
    """Deprecated: local-only flights are now upload queue items too."""
    raise ValueError("当前本地架次均视为待上传，不再支持放弃上传")


def create_sync_run(conn, run_type: str) -> int:
    if run_type not in {"push", "pull", "full"}:
        raise ValueError(f"Unsupported sync run type: {run_type}")
    conn.execute(
        "INSERT INTO sync_runs (run_type, status, started_at) VALUES (?, 'running', ?)",
        (run_type, now_text()),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def finish_sync_run(
    conn,
    run_id: int,
    status: str,
    *,
    summary: dict | None = None,
    error: dict | None = None,
) -> None:
    if status not in {"success", "failed"}:
        raise ValueError(f"Unsupported sync run status: {status}")
    conn.execute(
        """UPDATE sync_runs
           SET status=?, finished_at=?, summary_json=?, error_json=?
           WHERE id=?""",
        (
            status,
            now_text(),
            json.dumps(summary or {}, ensure_ascii=False),
            json.dumps(error or {}, ensure_ascii=False) if error else None,
            int(run_id),
        ),
    )


def _json_error(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def mark_upload_failed(conn, flight_ids: list[int], error: dict) -> None:
    clean_ids = sorted({int(fid) for fid in flight_ids})
    if not clean_ids:
        return
    placeholders = ",".join("?" for _ in clean_ids)
    conn.execute(
        f"""UPDATE flights
            SET sync_state='upload_failed',
                sync_error_json=?,
                updated_at=updated_at
            WHERE id IN ({placeholders})
              AND sync_state IN ('local_only', 'pending_upload', 'dirty', 'upload_failed', 'syncing')""",
        [_json_error(error), *clean_ids],
    )


def mark_base_upload_failed(conn, model_ids: list[int], aircraft_ids: list[int], error: dict) -> None:
    for table, ids in (("aircraft_models", model_ids), ("aircraft", aircraft_ids)):
        clean_ids = sorted({int(item_id) for item_id in ids})
        if not clean_ids:
            continue
        placeholders = ",".join("?" for _ in clean_ids)
        conn.execute(
            f"""UPDATE {table}
                SET sync_state='upload_failed',
                    sync_error_json=?,
                    updated_at=updated_at
                WHERE id IN ({placeholders})
                  AND sync_state IN ('local_only', 'pending_upload', 'dirty', 'upload_failed', 'syncing')""",
            [_json_error(error), *clean_ids],
        )


def mark_conflict(conn, flight_ids: list[int], report: dict) -> None:
    clean_ids = sorted({int(fid) for fid in flight_ids})
    if not clean_ids:
        return
    conflict_ids = {
        int(item["source_id"])
        for item in (report.get("conflicts") or [])
        if item.get("entity_type") == "flight" and item.get("source_id") is not None
    }
    if not conflict_ids:
        conflict_ids = set(clean_ids)
    target_ids = sorted(set(clean_ids) & conflict_ids)
    if not target_ids:
        target_ids = clean_ids
    placeholders = ",".join("?" for _ in target_ids)
    conn.execute(
        f"""UPDATE flights
            SET sync_state='conflict',
                sync_error_json=?
            WHERE id IN ({placeholders})""",
        [_json_error({"phase": "preflight", "report": report}), *target_ids],
    )


def mark_base_conflict(conn, report: dict) -> None:
    grouped = {"model": [], "aircraft": []}
    for item in report.get("conflicts") or []:
        entity_type = item.get("entity_type")
        source_id = item.get("source_id")
        if source_id is None:
            continue
        if entity_type == "model":
            grouped["model"].append(int(source_id))
        elif entity_type == "aircraft":
            grouped["aircraft"].append(int(source_id))
    for entity_type, table in (("model", "aircraft_models"), ("aircraft", "aircraft")):
        ids = sorted(set(grouped[entity_type]))
        if not ids:
            continue
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"""UPDATE {table}
                SET sync_state='conflict',
                    sync_error_json=?
                WHERE id IN ({placeholders})""",
            [_json_error({"phase": "preflight", "report": report}), *ids],
        )


def _apply_table_mappings(conn, table: str, mappings: list[dict], synced_at: str) -> int:
    applied = 0
    for item in mappings or []:
        source_id = item.get("source_id")
        server_id = item.get("server_id")
        if source_id is None or server_id is None:
            continue
        server_version = item.get("server_version") or 1
        conn.execute(
            f"""UPDATE {table}
                SET server_id=?,
                    server_version=?,
                    sync_state='synced',
                    last_sync_at=?,
                    sync_error_json=NULL
                WHERE id=?""",
            (int(server_id), int(server_version), synced_at, int(source_id)),
        )
        applied += 1
    return applied


def apply_push_report(conn, report: dict, selected_flight_ids: list[int]) -> dict:
    """Write server mappings from a successful push report into local sync fields."""
    mappings = report.get("mappings") or {}
    synced_at = now_text()
    applied = {
        "models": _apply_table_mappings(conn, "aircraft_models", mappings.get("models") or [], synced_at),
        "aircraft": _apply_table_mappings(conn, "aircraft", mappings.get("aircraft") or [], synced_at),
        "flights": _apply_table_mappings(conn, "flights", mappings.get("flights") or [], synced_at),
        "raw_files": _apply_table_mappings(conn, "flight_raw_files", mappings.get("raw_files") or [], synced_at),
    }
    mapped_flight_ids = {
        int(item["source_id"])
        for item in (mappings.get("flights") or [])
        if item.get("source_id") is not None and item.get("server_id") is not None
    }
    missing = sorted(set(int(fid) for fid in selected_flight_ids) - mapped_flight_ids)
    if missing:
        mark_upload_failed(
            conn,
            missing,
            {
                "phase": "writeback",
                "message": "Server push succeeded but did not return mappings for these flights",
                "flight_ids": missing,
            },
        )
    conn.execute(
        """INSERT INTO app_settings (key, value, updated_at)
           VALUES ('last_successful_push_at', ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (synced_at, synced_at),
    )
    return {"applied": applied, "missing_flight_ids": missing, "synced_at": synced_at}


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
