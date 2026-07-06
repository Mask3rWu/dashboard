"""Server-side preflight and push import for sync bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

from . import server_database as db


WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
SQLITE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_zip_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/")
    if raw.startswith("/") or WINDOWS_DRIVE_RE.match(raw):
        raise ValueError(f"Unsafe zip path: {path}")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Unsafe zip path: {path}")
    return "/".join(parts)


def _q_sqlite(identifier: str) -> str:
    if not SQLITE_IDENTIFIER_RE.match(identifier or ""):
        raise ValueError(f"Unsafe SQLite identifier: {identifier!r}")
    return f'"{identifier}"'


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _row_dict(row) -> dict[str, Any] | None:
    return dict(row._mapping) if row is not None else None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool_int(value: Any, default: bool = False) -> int:
    if value is None:
        return 1 if default else 0
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes", "on"} else 0
    return 1 if bool(value) else 0


def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _first_manifest_id(row: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _as_int(row.get(key))
        if value is not None:
            return value
    return None


def validate_manifest(manifest: dict[str, Any], *, require_push_batch: bool = True) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    if int(manifest.get("package_version") or 0) < 2:
        raise ValueError("Only package_version >= 2 is supported")
    if int(manifest.get("sync_protocol_version") or 0) != 1:
        raise ValueError("Unsupported sync_protocol_version")
    package_id = str(manifest.get("package_id") or "").strip()
    source_node_id = str(manifest.get("source_node_id") or "").strip()
    if not package_id:
        raise ValueError("manifest.package_id is required")
    if not source_node_id:
        raise ValueError("manifest.source_node_id is required")
    bundle_kind = str(manifest.get("bundle_kind") or "")
    if require_push_batch and bundle_kind != "push_batch":
        raise ValueError("Only bundle_kind=push_batch can be pushed to the server")
    parsed = manifest.get("parsed_data") or {}
    if parsed.get("format") != "sqlite":
        raise ValueError("manifest.parsed_data.format must be sqlite")
    parsed["path"] = _safe_zip_path(parsed.get("path") or "data/parsed.sqlite")
    for raw in manifest.get("raw_files") or []:
        if raw.get("package_path"):
            raw["package_path"] = _safe_zip_path(raw["package_path"])
        if raw.get("storage_rel_path"):
            raw["storage_rel_path"] = _safe_zip_path(raw["storage_rel_path"])
    return manifest


def existing_import_report(conn, package_id: str, source_node_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        db.text(
            """SELECT status, report_json, created_at
               FROM sync_imports
               WHERE package_id=:package_id AND source_node_id=:source_node_id"""
        ),
        {"package_id": package_id, "source_node_id": source_node_id},
    ).first()
    if not row:
        return None
    data = _row_dict(row) or {}
    try:
        report = json.loads(data.get("report_json") or "{}")
    except Exception:
        report = {"raw_report_json": data.get("report_json")}
    if isinstance(report, dict):
        duplicate_report = dict(report)
        duplicate_report["already_imported"] = True
        duplicate_report.setdefault("ok", data.get("status") == "success")
        duplicate_report.setdefault("status", data.get("status"))
        duplicate_report.setdefault("package_id", package_id)
        duplicate_report.setdefault("source_node_id", source_node_id)
        return duplicate_report
    return {
        "ok": data.get("status") == "success",
        "status": data.get("status"),
        "already_imported": True,
        "package_id": package_id,
        "source_node_id": source_node_id,
        "report": report,
    }


def _max_cursor(conn) -> int:
    row = conn.execute(db.text("SELECT MAX(`cursor`) AS cursor FROM sync_changes")).first()
    return int((row._mapping.get("cursor") if row else 0) or 0)


def _model_signature_from_manifest(model: dict[str, Any]) -> str | None:
    value = model.get("config_signature")
    return str(value) if value else None


def _find_model(conn, model: dict[str, Any]) -> dict[str, Any] | None:
    client_uid = model.get("client_uid")
    if client_uid:
        row = conn.execute(
            db.text("SELECT * FROM aircraft_models WHERE client_uid=:client_uid"),
            {"client_uid": client_uid},
        ).first()
        if row:
            return _row_dict(row)
    name = _clean_text(model.get("name")).strip()
    if name:
        row = conn.execute(
            db.text("SELECT * FROM aircraft_models WHERE name=:name"),
            {"name": name},
        ).first()
        if row:
            return _row_dict(row)
    return None


def _find_aircraft(conn, aircraft: dict[str, Any], server_model_id: int | None = None) -> dict[str, Any] | None:
    client_uid = aircraft.get("client_uid")
    if client_uid:
        row = conn.execute(
            db.text("SELECT * FROM aircraft WHERE client_uid=:client_uid"),
            {"client_uid": client_uid},
        ).first()
        if row:
            return _row_dict(row)
    if server_model_id is None:
        return None
    name = _clean_text(aircraft.get("name")).strip()
    if name:
        row = conn.execute(
            db.text("SELECT * FROM aircraft WHERE model_id=:model_id AND name=:name"),
            {"model_id": server_model_id, "name": name},
        ).first()
        if row:
            return _row_dict(row)
    return None


def _find_flight_by_client_uid(conn, client_uid: str | None) -> dict[str, Any] | None:
    if not client_uid:
        return None
    row = conn.execute(
        db.text("SELECT * FROM flights WHERE client_uid=:client_uid"),
        {"client_uid": client_uid},
    ).first()
    return _row_dict(row)


def _find_flight_by_business(
    conn,
    server_aircraft_id: int | None,
    flight_date: Any,
    session_key: Any,
) -> dict[str, Any] | None:
    if server_aircraft_id is None:
        return None
    row = conn.execute(
        db.text(
            """SELECT * FROM flights
               WHERE aircraft_id=:aircraft_id
                 AND ((flight_date IS NULL AND :flight_date IS NULL) OR flight_date=:flight_date)
                 AND session_key=:session_key"""
        ),
        {
            "aircraft_id": server_aircraft_id,
            "flight_date": flight_date or None,
            "session_key": _clean_text(session_key),
        },
    ).first()
    return _row_dict(row)


def _server_flight_raw_hashes(conn, flight_id: int) -> set[str]:
    rows = conn.execute(
        db.text(
            """SELECT fo.sha256
               FROM flight_raw_files frf
               JOIN file_objects fo ON fo.id = frf.file_object_id
               WHERE frf.flight_id=:flight_id"""
        ),
        {"flight_id": flight_id},
    ).fetchall()
    return {str(row._mapping["sha256"]) for row in rows}


def _manifest_raw_hashes_by_flight(manifest: dict[str, Any]) -> dict[int, set[str]]:
    grouped: dict[int, set[str]] = defaultdict(set)
    for raw in manifest.get("raw_files") or []:
        source_flight_id = _first_manifest_id(raw, "flight_id", "source_flight_id")
        sha = raw.get("sha256")
        if source_flight_id is not None and sha:
            grouped[source_flight_id].add(str(sha))
    return grouped


def _mapping_item(source_id: int | None, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "client_uid": row.get("client_uid"),
        "server_id": int(row["id"]),
        "server_version": int(row.get("version") or 1),
        "deleted_at": row.get("deleted_at"),
    }


def build_preflight_plan(conn, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest = validate_manifest(manifest)
    package_id = str(manifest["package_id"])
    source_node_id = str(manifest["source_node_id"])
    duplicate = existing_import_report(conn, package_id, source_node_id)
    if duplicate:
        return duplicate

    current_cursor = _max_cursor(conn)
    base_cursor = _as_int(manifest.get("base_server_cursor"))
    raw_hashes_by_flight = _manifest_raw_hashes_by_flight(manifest)

    model_by_source: dict[int, dict[str, Any]] = {}
    model_plan: list[dict[str, Any]] = []
    for model in manifest.get("models") or []:
        source_id = _first_manifest_id(model, "id", "source_id")
        existing = _find_model(conn, model)
        action = "create"
        conflict_reason = None
        if existing:
            action = "existing"
            signature = _model_signature_from_manifest(model)
            if (
                signature
                and existing.get("config_signature")
                and signature != existing.get("config_signature")
                and model.get("client_uid") != existing.get("client_uid")
            ):
                action = "conflict"
                conflict_reason = "model_name_config_mismatch"
        item = {
            "entity_type": "model",
            "source_id": source_id,
            "client_uid": model.get("client_uid"),
            "name": model.get("name"),
            "action": action,
            "server_id": existing.get("id") if existing else None,
            "server_version": existing.get("version") if existing else None,
            "reason": conflict_reason,
        }
        model_plan.append(item)
        if source_id is not None and existing:
            model_by_source[source_id] = existing

    aircraft_by_source: dict[int, dict[str, Any]] = {}
    aircraft_plan: list[dict[str, Any]] = []
    for aircraft in manifest.get("aircraft") or []:
        source_id = _first_manifest_id(aircraft, "id", "source_id")
        source_model_id = _first_manifest_id(aircraft, "model_id", "source_model_id")
        server_model = model_by_source.get(source_model_id) if source_model_id is not None else None
        existing = _find_aircraft(conn, aircraft, _as_int(server_model.get("id")) if server_model else None)
        action = "existing" if existing else "create"
        item = {
            "entity_type": "aircraft",
            "source_id": source_id,
            "client_uid": aircraft.get("client_uid"),
            "name": aircraft.get("name"),
            "source_model_id": source_model_id,
            "action": action,
            "server_id": existing.get("id") if existing else None,
            "server_version": existing.get("version") if existing else None,
        }
        aircraft_plan.append(item)
        if source_id is not None and existing:
            aircraft_by_source[source_id] = existing

    flight_plan: list[dict[str, Any]] = []
    for flight in manifest.get("flights") or []:
        source_id = _first_manifest_id(flight, "id", "source_id")
        source_aircraft_id = _first_manifest_id(flight, "aircraft_id", "source_aircraft_id")
        server_aircraft = aircraft_by_source.get(source_aircraft_id) if source_aircraft_id is not None else None
        existing = _find_flight_by_client_uid(conn, flight.get("client_uid"))
        matched_by = "client_uid" if existing else None
        if not existing:
            existing = _find_flight_by_business(
                conn,
                _as_int(server_aircraft.get("id")) if server_aircraft else None,
                flight.get("flight_date"),
                flight.get("session_key"),
            )
            matched_by = "business_key" if existing else None

        action = "create"
        reason = None
        if existing:
            package_hashes = raw_hashes_by_flight.get(source_id or -1, set())
            server_hashes = _server_flight_raw_hashes(conn, int(existing["id"]))
            if matched_by == "business_key" and package_hashes and server_hashes and package_hashes != server_hashes:
                action = "conflict"
                reason = "business_key_raw_hash_mismatch"
            elif existing.get("deleted_at") is not None:
                action = "conflict"
                reason = "server_deleted"
            else:
                action = "existing"

        flight_plan.append(
            {
                "entity_type": "flight",
                "source_id": source_id,
                "client_uid": flight.get("client_uid"),
                "name": flight.get("name"),
                "source_aircraft_id": source_aircraft_id,
                "flight_date": flight.get("flight_date"),
                "session_key": flight.get("session_key"),
                "action": action,
                "server_id": existing.get("id") if existing else None,
                "server_version": existing.get("version") if existing else None,
                "matched_by": matched_by,
                "reason": reason,
            }
        )

    conflicts = [
        item for item in [*model_plan, *aircraft_plan, *flight_plan] if item.get("action") == "conflict"
    ]
    return {
        "ok": not conflicts,
        "status": "conflict" if conflicts else "ready",
        "package_id": package_id,
        "source_node_id": source_node_id,
        "current_cursor": current_cursor,
        "base_server_cursor": manifest.get("base_server_cursor"),
        "needs_pull": base_cursor is not None and base_cursor < current_cursor,
        "summary": {
            "models": _count_actions(model_plan),
            "aircraft": _count_actions(aircraft_plan),
            "flights": _count_actions(flight_plan),
            "conflicts": len(conflicts),
        },
        "models": model_plan,
        "aircraft": aircraft_plan,
        "flights": flight_plan,
        "conflicts": conflicts,
    }


def _count_actions(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        action = str(item.get("action") or "unknown")
        counts[action] = counts.get(action, 0) + 1
    counts["total"] = len(items)
    return counts


def _extract_bundle(bundle_path: str, tmp_dir: str) -> tuple[dict[str, Any], str]:
    with zipfile.ZipFile(bundle_path, "r") as zf:
        names = zf.namelist()
        if "manifest.json" not in names:
            raise ValueError("Bundle is missing manifest.json")
        for name in names:
            _safe_zip_path(name)
        manifest = validate_manifest(json.loads(zf.read("manifest.json").decode("utf-8")))
        parsed = manifest.get("parsed_data") or {}
        parsed_path = _safe_zip_path(parsed.get("path") or "data/parsed.sqlite")
        if parsed_path not in names:
            raise ValueError(f"Bundle is missing parsed sqlite: {parsed_path}")
        parsed_abs = os.path.join(tmp_dir, "parsed.sqlite")
        with zf.open(parsed_path) as src, open(parsed_abs, "wb") as dst:
            shutil.copyfileobj(src, dst)
        expected_size = _as_int(parsed.get("size_bytes"))
        if expected_size is not None and os.path.getsize(parsed_abs) != expected_size:
            raise ValueError("parsed.sqlite size does not match manifest")
        expected_sha = parsed.get("sha256")
        if expected_sha and _sha256_file(parsed_abs) != expected_sha:
            raise ValueError("parsed.sqlite sha256 does not match manifest")
    return manifest, parsed_abs


def _sqlite_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not row:
        return []
    rows = conn.execute(f"SELECT * FROM {_q_sqlite(table)} ORDER BY source_id").fetchall()
    return [dict(r) for r in rows]


def _build_source_config(parsed: sqlite3.Connection, source_model_id: int) -> dict[str, Any]:
    model_rows = _sqlite_rows(parsed, "aircraft_models")
    model = next((row for row in model_rows if _as_int(row.get("source_id")) == source_model_id), None)
    if not model:
        raise ValueError(f"Model {source_model_id} not found in parsed sqlite")

    data_types: dict[str, Any] = {}
    dtr_rows = [
        row for row in _sqlite_rows(parsed, "data_table_registry")
        if _as_int(row.get("source_model_id")) == source_model_id
    ]
    col_rows = [
        row for row in _sqlite_rows(parsed, "column_registry")
        if _as_int(row.get("source_model_id")) == source_model_id
    ]
    for dtr in dtr_rows:
        data_type_key = db.validate_data_type_key(str(dtr.get("data_type_key") or ""))
        try:
            patterns = json.loads(dtr.get("file_patterns") or "[]")
        except Exception:
            patterns = []
        columns = []
        for col in sorted(
            [c for c in col_rows if c.get("data_type_key") == data_type_key],
            key=lambda c: _as_int(c.get("ordinal")) or 0,
        ):
            columns.append(
                {
                    "name": col.get("column_name"),
                    "label": col.get("display_label"),
                    "unit": col.get("unit") or "",
                    "type": col.get("data_type") or "REAL",
                    "ordinal": _as_int(col.get("ordinal")),
                    "scale_factor": float(col.get("scale_factor") or 1.0),
                }
            )
        data_types[data_type_key] = {
            "display_label": dtr.get("display_label") or data_type_key,
            "file_patterns": patterns if isinstance(patterns, list) else [],
            "is_alert": bool(dtr.get("is_alert")),
            "columns": columns,
        }
    return {
        "name": model.get("name"),
        "client_uid": model.get("client_uid"),
        "source_node_id": model.get("source_node_id"),
        "has_header": bool(model.get("has_header", 1)),
        "has_uav_send_id": bool(model.get("has_uav_send_id", 0)),
        "extract_serial_from_path": bool(model.get("extract_serial_from_path", 0)),
        "data_types": data_types,
    }


def _ensure_model(conn, parsed: sqlite3.Connection, source_model: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    existing = _find_model(conn, source_model)
    if existing:
        _ensure_model_registry(conn, parsed, int(source_model["source_id"]), int(existing["id"]))
        return existing, False
    created = db.create_model(conn, _build_source_config(parsed, int(source_model["source_id"])))
    row = conn.execute(
        db.text("SELECT * FROM aircraft_models WHERE id=:id"),
        {"id": int(created["id"])},
    ).first()
    return _row_dict(row) or {"id": int(created["id"]), "version": 1}, True


def _ensure_model_registry(
    conn,
    parsed: sqlite3.Connection,
    source_model_id: int,
    server_model_id: int,
) -> None:
    config = _build_source_config(parsed, source_model_id)
    now = db.utcnow()
    for data_type_key, dt_def in config.get("data_types", {}).items():
        data_type_key = db.validate_data_type_key(data_type_key)
        table_name = db.server_data_table_name(server_model_id, data_type_key)
        db.create_dynamic_table(conn, server_model_id, data_type_key, dt_def.get("columns") or [])
        existing = conn.execute(
            db.text(
                """SELECT id FROM data_table_registry
                   WHERE model_id=:model_id AND data_type_key=:data_type_key"""
            ),
            {"model_id": server_model_id, "data_type_key": data_type_key},
        ).first()
        if not existing:
            conn.execute(
                db.text(
                    """INSERT INTO data_table_registry
                         (model_id, data_type_key, table_name, display_label, file_patterns,
                          is_alert, created_at, updated_at)
                       VALUES
                         (:model_id, :data_type_key, :table_name, :display_label, :file_patterns,
                          :is_alert, :created_at, :updated_at)"""
                ),
                {
                    "model_id": server_model_id,
                    "data_type_key": data_type_key,
                    "table_name": table_name,
                    "display_label": dt_def.get("display_label") or data_type_key,
                    "file_patterns": json.dumps(dt_def.get("file_patterns") or [], ensure_ascii=False),
                    "is_alert": 1 if dt_def.get("is_alert") else 0,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        for index, raw_col in enumerate(dt_def.get("columns") or [], start=1):
            col = db.normalize_column(raw_col, index)
            existing_col = conn.execute(
                db.text(
                    """SELECT id FROM column_registry
                       WHERE model_id=:model_id AND table_name=:table_name AND column_name=:column_name"""
                ),
                {
                    "model_id": server_model_id,
                    "table_name": table_name,
                    "column_name": col["name"],
                },
            ).first()
            if existing_col:
                continue
            conn.execute(
                db.text(
                    """INSERT INTO column_registry
                         (model_id, data_type_key, table_name, column_name, display_label,
                          unit, data_type, ordinal, is_numeric, scale_factor, created_at, updated_at)
                       VALUES
                         (:model_id, :data_type_key, :table_name, :column_name, :display_label,
                          :unit, :data_type, :ordinal, :is_numeric, :scale_factor,
                          :created_at, :updated_at)"""
                ),
                {
                    "model_id": server_model_id,
                    "data_type_key": data_type_key,
                    "table_name": table_name,
                    "column_name": col["name"],
                    "display_label": col["display_label"],
                    "unit": col["unit"],
                    "data_type": col["data_type"],
                    "ordinal": col["ordinal"],
                    "is_numeric": col["is_numeric"],
                    "scale_factor": col["scale_factor"],
                    "created_at": now,
                    "updated_at": now,
                },
            )


def _insert_change(conn, entity_type: str, entity_id: int, package_id: str, source_node_id: str) -> None:
    conn.execute(
        db.text(
            """INSERT INTO sync_changes
                 (entity_type, entity_id, change_type, entity_version, changed_at,
                  changed_by_node_id, package_id)
               VALUES
                 (:entity_type, :entity_id, 'create', 1, :changed_at,
                  :changed_by_node_id, :package_id)"""
        ),
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "changed_at": db.utcnow(),
            "changed_by_node_id": source_node_id,
            "package_id": package_id,
        },
    )


def _ensure_aircraft(
    conn,
    aircraft: dict[str, Any],
    server_model_id: int,
    package_id: str,
    source_node_id: str,
) -> tuple[dict[str, Any], bool]:
    existing = _find_aircraft(conn, aircraft, server_model_id)
    if existing:
        return existing, False
    now = db.utcnow()
    conn.execute(
        db.text(
            """INSERT INTO aircraft
                 (client_uid, source_node_id, model_id, name, created_at, updated_at)
               VALUES
                 (:client_uid, :source_node_id, :model_id, :name, :created_at, :updated_at)"""
        ),
        {
            "client_uid": aircraft.get("client_uid"),
            "source_node_id": aircraft.get("source_node_id") or source_node_id,
            "model_id": server_model_id,
            "name": _clean_text(aircraft.get("name")),
            "created_at": aircraft.get("created_at") or now,
            "updated_at": aircraft.get("updated_at") or now,
        },
    )
    row = conn.execute(db.text("SELECT * FROM aircraft WHERE id=LAST_INSERT_ID()")).first()
    created = _row_dict(row) or {}
    _insert_change(conn, "aircraft", int(created["id"]), package_id, source_node_id)
    return created, True


FLIGHT_COLUMNS = [
    "client_uid",
    "source_node_id",
    "aircraft_id",
    "name",
    "source_path",
    "session_key",
    "flight_date",
    "start_time",
    "end_time",
    "duration_sec",
    "total_rows",
    "record_daily_duration_min",
    "record_batch_name",
    "record_location",
    "record_payload",
    "record_weather",
    "record_fuel_amount",
    "record_takeoff_weight",
    "record_altitude",
    "record_wind_speed",
    "record_note",
    "created_at",
    "updated_at",
]


def _ensure_flight(
    conn,
    flight: dict[str, Any],
    server_aircraft_id: int,
    package_id: str,
    source_node_id: str,
) -> tuple[dict[str, Any], bool]:
    existing = _find_flight_by_client_uid(conn, flight.get("client_uid"))
    if not existing:
        existing = _find_flight_by_business(
            conn,
            server_aircraft_id,
            flight.get("flight_date"),
            flight.get("session_key"),
        )
    if existing:
        return existing, False
    now = db.utcnow()
    values = {
        "client_uid": flight.get("client_uid"),
        "source_node_id": flight.get("source_node_id") or source_node_id,
        "aircraft_id": server_aircraft_id,
        "name": _clean_text(flight.get("name")),
        "source_path": flight.get("source_path"),
        "session_key": _clean_text(flight.get("session_key")),
        "flight_date": flight.get("flight_date") or None,
        "start_time": flight.get("start_time") or None,
        "end_time": flight.get("end_time") or None,
        "duration_sec": flight.get("duration_sec"),
        "total_rows": _as_int(flight.get("total_rows")) or 0,
        "record_daily_duration_min": flight.get("record_daily_duration_min"),
        "record_batch_name": _clean_text(flight.get("record_batch_name")),
        "record_location": _clean_text(flight.get("record_location")),
        "record_payload": _clean_text(flight.get("record_payload")),
        "record_weather": _clean_text(flight.get("record_weather")),
        "record_fuel_amount": flight.get("record_fuel_amount"),
        "record_takeoff_weight": flight.get("record_takeoff_weight"),
        "record_altitude": flight.get("record_altitude"),
        "record_wind_speed": flight.get("record_wind_speed"),
        "record_note": _clean_text(flight.get("record_note")),
        "created_at": flight.get("created_at") or flight.get("import_time") or now,
        "updated_at": flight.get("updated_at") or now,
    }
    columns_sql = ", ".join(FLIGHT_COLUMNS)
    params_sql = ", ".join(f":{col}" for col in FLIGHT_COLUMNS)
    conn.execute(
        db.text(f"INSERT INTO flights ({columns_sql}) VALUES ({params_sql})"),
        values,
    )
    row = conn.execute(db.text("SELECT * FROM flights WHERE id=LAST_INSERT_ID()")).first()
    created = _row_dict(row) or {}
    _insert_change(conn, "flight", int(created["id"]), package_id, source_node_id)
    return created, True


def _copy_raw_object_from_zip(
    bundle_path: str,
    package_path: str,
    storage_rel_path: str,
    expected_sha: str,
    expected_size: int,
) -> str:
    storage_rel_path = _safe_zip_path(storage_rel_path)
    destination = os.path.abspath(os.path.join(db.SERVER_DATA_DIR, "objects", storage_rel_path))
    root = os.path.abspath(os.path.join(db.SERVER_DATA_DIR, "objects"))
    if os.path.commonpath([destination, root]) != root:
        raise ValueError("raw object path escapes object root")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if os.path.exists(destination):
        if os.path.getsize(destination) != expected_size or _sha256_file(destination) != expected_sha:
            raise ValueError(f"stored raw object hash/size mismatch: {storage_rel_path}")
        return storage_rel_path

    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".object", dir=os.path.dirname(destination))
    os.close(fd)
    try:
        with zipfile.ZipFile(bundle_path, "r") as zf:
            with zf.open(package_path) as src, open(tmp_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
        if os.path.getsize(tmp_path) != expected_size:
            raise ValueError("raw object size does not match manifest")
        if _sha256_file(tmp_path) != expected_sha:
            raise ValueError("raw object sha256 does not match manifest")
        os.replace(tmp_path, destination)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return storage_rel_path


def _ensure_file_object(conn, raw: dict[str, Any], bundle_path: str) -> tuple[dict[str, Any] | None, str | None, bool]:
    expected_sha = str(raw.get("sha256") or "").lower()
    expected_size = _as_int(raw.get("size_bytes"))
    if not re.match(r"^[0-9a-fA-F]{64}$", expected_sha) or expected_size is None:
        return None, "raw object has invalid sha256 or size", False
    row = conn.execute(
        db.text("SELECT * FROM file_objects WHERE sha256=:sha256"),
        {"sha256": expected_sha},
    ).first()
    if row:
        return _row_dict(row), None, False

    package_path = raw.get("package_path") or f"objects/{raw.get('storage_rel_path') or ''}"
    package_path = _safe_zip_path(package_path)
    storage_rel_path = raw.get("storage_rel_path") or f"sha256/{expected_sha[:2]}/{expected_sha}"
    storage_rel_path = _safe_zip_path(storage_rel_path)
    try:
        stored_rel = _copy_raw_object_from_zip(
            bundle_path,
            package_path,
            storage_rel_path,
            expected_sha.lower(),
            expected_size,
        )
    except Exception as exc:
        return None, str(exc), False

    conn.execute(
        db.text(
            """INSERT INTO file_objects (sha256, size_bytes, storage_rel_path, created_at)
               VALUES (:sha256, :size_bytes, :storage_rel_path, :created_at)"""
        ),
        {
            "sha256": expected_sha.lower(),
            "size_bytes": expected_size,
            "storage_rel_path": stored_rel,
            "created_at": db.utcnow(),
        },
    )
    row = conn.execute(db.text("SELECT * FROM file_objects WHERE id=LAST_INSERT_ID()")).first()
    return _row_dict(row), None, True


def _attach_raw_file(conn, raw: dict[str, Any], server_flight_id: int, file_object_id: int) -> tuple[int | None, bool]:
    existing = conn.execute(
        db.text(
            """SELECT id FROM flight_raw_files
               WHERE flight_id=:flight_id
                 AND file_object_id=:file_object_id
                 AND original_rel_path=:original_rel_path"""
        ),
        {
            "flight_id": server_flight_id,
            "file_object_id": file_object_id,
            "original_rel_path": _clean_text(raw.get("original_rel_path")),
        },
    ).first()
    if existing:
        return int(existing._mapping["id"]), False
    conn.execute(
        db.text(
            """INSERT INTO flight_raw_files
                 (flight_id, file_object_id, original_name, original_rel_path,
                  data_type_key, source_mtime, created_at)
               VALUES
                 (:flight_id, :file_object_id, :original_name, :original_rel_path,
                  :data_type_key, :source_mtime, :created_at)"""
        ),
        {
            "flight_id": server_flight_id,
            "file_object_id": file_object_id,
            "original_name": _clean_text(raw.get("original_name")),
            "original_rel_path": _clean_text(raw.get("original_rel_path")),
            "data_type_key": raw.get("data_type_key"),
            "source_mtime": raw.get("source_mtime"),
            "created_at": raw.get("created_at") or db.utcnow(),
        },
    )
    row = conn.execute(db.text("SELECT LAST_INSERT_ID() AS id")).first()
    return int(row._mapping["id"]), True


def _insert_dynamic_rows(
    conn,
    parsed: sqlite3.Connection,
    source_model_id: int,
    server_model_id: int,
    flight_map: dict[int, int],
) -> int:
    inserted = 0
    dtr_rows = [
        row for row in _sqlite_rows(parsed, "data_table_registry")
        if _as_int(row.get("source_model_id")) == source_model_id
    ]
    for dtr in dtr_rows:
        source_table = str(dtr.get("table_name") or "")
        source_table_q = _q_sqlite(source_table)
        data_type_key = db.validate_data_type_key(str(dtr.get("data_type_key") or ""))
        server_table = db.server_data_table_name(server_model_id, data_type_key)
        server_table_q = db.quote_identifier(server_table)
        col_rows = sorted(
            [
                row for row in _sqlite_rows(parsed, "column_registry")
                if _as_int(row.get("source_model_id")) == source_model_id
                and row.get("data_type_key") == data_type_key
            ],
            key=lambda c: _as_int(c.get("ordinal")) or 0,
        )
        columns = ["flight_id", "time_str", "time_sec"] + [
            db.validate_identifier(str(col.get("column_name") or ""), "column name")
            for col in col_rows
        ]
        source_columns = ["source_flight_id", "time_str", "time_sec"] + columns[3:]
        query = f"SELECT {', '.join(_q_sqlite(c) for c in source_columns)} FROM {source_table_q}"
        for row in parsed.execute(query).fetchall():
            source_flight_id = _as_int(row["source_flight_id"])
            server_flight_id = flight_map.get(source_flight_id or -1)
            if server_flight_id is None:
                continue
            values = {"flight_id": server_flight_id}
            for col in columns[1:]:
                values[col] = row[col]
            placeholders = ", ".join(f":{col}" for col in columns)
            conn.execute(
                db.text(
                    f"INSERT INTO {server_table_q} "
                    f"({', '.join(db.quote_identifier(c) for c in columns)}) "
                    f"VALUES ({placeholders})"
                ),
                values,
            )
            inserted += 1
    return inserted


def import_push_bundle(conn, bundle_path: str, imported_by: int | None = None) -> dict[str, Any]:
    bundle_abs = os.path.abspath(bundle_path)
    tmp_dir = tempfile.mkdtemp(prefix="flightanalyzer_server_push_")
    try:
        manifest, parsed_path = _extract_bundle(bundle_abs, tmp_dir)
        package_id = str(manifest["package_id"])
        source_node_id = str(manifest["source_node_id"])
        duplicate = existing_import_report(conn, package_id, source_node_id)
        if duplicate:
            return duplicate

        preflight = build_preflight_plan(conn, manifest)
        if preflight.get("conflicts"):
            report = {
                "ok": False,
                "status": "conflict",
                "package_id": package_id,
                "source_node_id": source_node_id,
                "preflight": preflight,
                "conflicts": preflight.get("conflicts") or [],
                "warnings": [],
                "mappings": {"models": [], "aircraft": [], "flights": [], "file_objects": [], "raw_files": []},
            }
            _record_import(conn, package_id, source_node_id, imported_by, "conflict", report)
            return report

        parsed = sqlite3.connect(parsed_path)
        parsed.row_factory = sqlite3.Row
        try:
            report = _import_parsed_bundle(conn, parsed, bundle_abs, manifest, imported_by, preflight)
        finally:
            parsed.close()
        _record_import(conn, package_id, source_node_id, imported_by, report["status"], report)
        _copy_bundle_archive(bundle_abs, package_id, source_node_id)
        return report
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _import_parsed_bundle(
    conn,
    parsed: sqlite3.Connection,
    bundle_path: str,
    manifest: dict[str, Any],
    imported_by: int | None,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    package_id = str(manifest["package_id"])
    source_node_id = str(manifest["source_node_id"])
    model_rows = _sqlite_rows(parsed, "aircraft_models")
    aircraft_rows = _sqlite_rows(parsed, "aircraft")
    flight_rows = _sqlite_rows(parsed, "flights")

    model_map: dict[int, dict[str, Any]] = {}
    aircraft_map: dict[int, dict[str, Any]] = {}
    flight_map: dict[int, dict[str, Any]] = {}
    created_source_flights: set[int] = set()

    imported_counts = {
        "models": 0,
        "aircraft": 0,
        "flights": 0,
        "raw_objects": 0,
        "raw_links": 0,
        "dynamic_rows": 0,
    }
    existing_counts = {"models": 0, "aircraft": 0, "flights": 0, "raw_objects": 0, "raw_links": 0}
    warnings: list[dict[str, Any]] = []
    mappings = {"models": [], "aircraft": [], "flights": [], "file_objects": [], "raw_files": []}
    imported_raw_object_ids: set[int] = set()
    existing_raw_object_ids: set[int] = set()

    for source_model in model_rows:
        source_id = int(source_model["source_id"])
        server_model, created = _ensure_model(conn, parsed, source_model)
        model_map[source_id] = server_model
        if created:
            imported_counts["models"] += 1
        else:
            existing_counts["models"] += 1
        mappings["models"].append(_mapping_item(source_id, server_model))

    for source_aircraft in aircraft_rows:
        source_id = int(source_aircraft["source_id"])
        source_model_id = int(source_aircraft["source_model_id"])
        server_model = model_map[source_model_id]
        server_aircraft, created = _ensure_aircraft(
            conn,
            source_aircraft,
            int(server_model["id"]),
            package_id,
            source_node_id,
        )
        aircraft_map[source_id] = server_aircraft
        if created:
            imported_counts["aircraft"] += 1
        else:
            existing_counts["aircraft"] += 1
        mappings["aircraft"].append(_mapping_item(source_id, server_aircraft))

    for source_flight in flight_rows:
        source_id = int(source_flight["source_id"])
        source_aircraft_id = int(source_flight["source_aircraft_id"])
        server_aircraft = aircraft_map[source_aircraft_id]
        server_flight, created = _ensure_flight(
            conn,
            source_flight,
            int(server_aircraft["id"]),
            package_id,
            source_node_id,
        )
        flight_map[source_id] = server_flight
        if created:
            imported_counts["flights"] += 1
            created_source_flights.add(source_id)
        else:
            existing_counts["flights"] += 1
        mappings["flights"].append(_mapping_item(source_id, server_flight))

    for source_model_id, server_model in model_map.items():
        rows = _insert_dynamic_rows(
            conn,
            parsed,
            source_model_id,
            int(server_model["id"]),
            {source_id: int(row["id"]) for source_id, row in flight_map.items() if source_id in created_source_flights},
        )
        imported_counts["dynamic_rows"] += rows

    for raw in manifest.get("raw_files") or []:
        source_flight_id = _first_manifest_id(raw, "flight_id", "source_flight_id")
        if source_flight_id is None or source_flight_id not in flight_map:
            continue
        file_object, warning, object_created = _ensure_file_object(conn, raw, bundle_path)
        if warning:
            warnings.append(
                {
                    "type": "raw_object",
                    "source_flight_id": source_flight_id,
                    "sha256": raw.get("sha256"),
                    "message": warning,
                }
            )
            continue
        if not file_object:
            continue
        file_object_id = int(file_object["id"])
        if object_created:
            imported_raw_object_ids.add(file_object_id)
        else:
            existing_raw_object_ids.add(file_object_id)
        raw_link_id, created = _attach_raw_file(
            conn,
            raw,
            int(flight_map[source_flight_id]["id"]),
            file_object_id,
        )
        if created:
            imported_counts["raw_links"] += 1
        else:
            existing_counts["raw_links"] += 1
        mappings["file_objects"].append(
            {
                "source_id": raw.get("file_object_id"),
                "client_uid": raw.get("file_object_client_uid"),
                "server_id": file_object_id,
                "sha256": file_object["sha256"],
            }
        )
        mappings["raw_files"].append(
            {
                "source_id": raw.get("id"),
                "client_uid": raw.get("client_uid"),
                "server_id": raw_link_id,
                "source_flight_id": source_flight_id,
                "server_flight_id": int(flight_map[source_flight_id]["id"]),
            }
        )

    imported_counts["raw_objects"] = len(imported_raw_object_ids)
    existing_counts["raw_objects"] = len(existing_raw_object_ids - imported_raw_object_ids)

    conn.execute(
        db.text(
            """INSERT INTO sync_clients (node_id, last_seen_at, last_push_cursor)
               VALUES (:node_id, :last_seen_at, :last_push_cursor)
               ON DUPLICATE KEY UPDATE
                   last_seen_at=VALUES(last_seen_at),
                   last_push_cursor=VALUES(last_push_cursor)"""
        ),
        {
            "node_id": source_node_id,
            "last_seen_at": db.utcnow(),
            "last_push_cursor": _max_cursor(conn),
        },
    )

    return {
        "ok": True,
        "status": "success",
        "package_id": package_id,
        "source_node_id": source_node_id,
        "imported_by": imported_by,
        "current_cursor": _max_cursor(conn),
        "preflight": preflight,
        "imported": imported_counts,
        "existing": existing_counts,
        "warnings": warnings,
        "mappings": mappings,
    }


def _record_import(
    conn,
    package_id: str,
    source_node_id: str,
    imported_by: int | None,
    status: str,
    report: dict[str, Any],
) -> None:
    conn.execute(
        db.text(
            """INSERT INTO sync_imports
                 (package_id, source_node_id, imported_by, status, report_json, created_at)
               VALUES
                 (:package_id, :source_node_id, :imported_by, :status, :report_json, :created_at)"""
        ),
        {
            "package_id": package_id,
            "source_node_id": source_node_id,
            "imported_by": imported_by,
            "status": status,
            "report_json": json.dumps(report, ensure_ascii=False, default=_json_default),
            "created_at": db.utcnow(),
        },
    )


def _copy_bundle_archive(bundle_path: str, package_id: str, source_node_id: str) -> None:
    bundles_dir = os.path.join(db.SERVER_DATA_DIR, "bundles")
    os.makedirs(bundles_dir, exist_ok=True)
    safe_package = re.sub(r"[^A-Za-z0-9_.-]+", "_", package_id)
    safe_node = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_node_id)
    destination = os.path.join(bundles_dir, f"{safe_node}_{safe_package}.fapkg")
    if not os.path.exists(destination):
        shutil.copy2(bundle_path, destination)
