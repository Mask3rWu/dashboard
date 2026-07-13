"""Server-side preflight and push import for sync bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

from . import server_database as db


WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
WINDOWS_INVALID_CHARS = set('<>:"\\|?*')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
DYNAMIC_INSERT_BATCH_SIZE = 1000


def _safe_zip_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/")
    if raw.startswith("/") or WINDOWS_DRIVE_RE.match(raw):
        raise ValueError(f"Unsafe zip path: {path}")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Unsafe zip path: {path}")
    return "/".join(parts)


def _safe_part(value: Any, fallback: str = "_") -> str:
    cleaned = []
    for char in str(value or "").strip():
        if ord(char) < 32 or char in WINDOWS_INVALID_CHARS:
            cleaned.append("_")
        else:
            cleaned.append(char)
    text = "".join(cleaned).strip(" .")
    if not text or text in {".", ".."}:
        text = fallback
    if text.upper() in WINDOWS_RESERVED_NAMES:
        text = f"{text}_"
    return text


def _date_prefix(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    return digits[:8] if len(digits) >= 8 else "undated"


def _server_raw_abs_path(storage_rel_path: str) -> str:
    root = os.path.abspath(os.path.join(db.SERVER_DATA_DIR, "raw_files"))
    abs_path = os.path.abspath(os.path.join(root, storage_rel_path))
    if os.path.commonpath([root, abs_path]) != root:
        raise ValueError("raw file path escapes raw storage root")
    return abs_path


def _q_sqlite(identifier: str) -> str:
    value = str(identifier or "")
    if not value or "\x00" in value:
        raise ValueError(f"Unsafe SQLite identifier: {identifier!r}")
    return f'"{value.replace(chr(34), chr(34) + chr(34))}"'


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
    row = conn.execute(db.text("SELECT MAX(`cursor`) AS max_cursor FROM sync_changes")).first()
    return int((row._mapping.get("max_cursor") if row else 0) or 0)


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


def _find_model_by_name(conn, name: str) -> dict[str, Any] | None:
    row = conn.execute(
        db.text("SELECT * FROM aircraft_models WHERE name=:name"),
        {"name": _clean_text(name).strip()},
    ).first()
    return _row_dict(row)


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


def _find_aircraft_by_name(conn, server_model_id: int, name: str) -> dict[str, Any] | None:
    row = conn.execute(
        db.text("SELECT * FROM aircraft WHERE model_id=:model_id AND name=:name"),
        {"model_id": server_model_id, "name": _clean_text(name).strip()},
    ).first()
    return _row_dict(row)


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
            "SELECT sha256 FROM flight_raw_files WHERE flight_id=:flight_id"
        ),
        {"flight_id": flight_id},
    ).fetchall()
    return {str(row._mapping["sha256"]) for row in rows}


def _manifest_server_version(row: dict[str, Any]) -> int | None:
    value = row.get("server_version")
    if value in (None, ""):
        value = row.get("version")
    return _as_int(value)


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
            known_version = _manifest_server_version(model)
            current_version = _as_int(existing.get("version")) or 1
            target_name = _clean_text(model.get("name")).strip()
            signature = _model_signature_from_manifest(model)
            if (
                signature
                and existing.get("config_signature")
                and signature != existing.get("config_signature")
                and model.get("client_uid") != existing.get("client_uid")
            ):
                action = "conflict"
                conflict_reason = "model_name_config_mismatch"
            elif known_version is not None and current_version > known_version:
                action = "conflict"
                conflict_reason = "server_changed_since_last_sync"
            else:
                name_owner = _find_model_by_name(conn, target_name) if target_name else None
                if name_owner and int(name_owner["id"]) != int(existing["id"]):
                    action = "conflict"
                    conflict_reason = "model_name_conflict"
                elif target_name and target_name != existing.get("name"):
                    action = "update_metadata"
                else:
                    action = "existing"
        item = {
            "entity_type": "model",
            "source_id": source_id,
            "client_uid": model.get("client_uid"),
            "name": model.get("name"),
            "action": action,
            "server_id": existing.get("id") if existing else None,
            "server_name": existing.get("name") if existing else None,
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
        conflict_reason = None
        if existing:
            known_version = _manifest_server_version(aircraft)
            current_version = _as_int(existing.get("version")) or 1
            target_name = _clean_text(aircraft.get("name")).strip()
            server_model_id = _as_int(server_model.get("id")) if server_model else None
            if known_version is not None and current_version > known_version:
                action = "conflict"
                conflict_reason = "server_changed_since_last_sync"
            elif server_model_id is not None:
                name_owner = _find_aircraft_by_name(conn, server_model_id, target_name) if target_name else None
                if name_owner and int(name_owner["id"]) != int(existing["id"]):
                    action = "conflict"
                    conflict_reason = "aircraft_name_conflict"
                elif target_name and target_name != existing.get("name"):
                    action = "update_metadata"
        item = {
            "entity_type": "aircraft",
            "source_id": source_id,
            "client_uid": aircraft.get("client_uid"),
            "name": aircraft.get("name"),
            "source_model_id": source_model_id,
            "action": action,
            "server_id": existing.get("id") if existing else None,
            "server_name": existing.get("name") if existing else None,
            "server_version": existing.get("version") if existing else None,
            "reason": conflict_reason,
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
            known_version = _manifest_server_version(flight)
            current_version = _as_int(existing.get("version")) or 1
            package_hashes = raw_hashes_by_flight.get(source_id or -1, set())
            server_hashes = _server_flight_raw_hashes(conn, int(existing["id"]))
            if known_version is not None and current_version > known_version:
                action = "conflict"
                reason = "server_changed_since_last_sync"
            elif matched_by == "business_key" and package_hashes and server_hashes and package_hashes != server_hashes:
                action = "conflict"
                reason = "business_key_raw_hash_mismatch"
            elif existing.get("deleted_at") is not None:
                action = "restore"
                reason = "server_deleted"
            else:
                action = "update_metadata"

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


def _ensure_model(
    conn,
    parsed: sqlite3.Connection,
    source_model: dict[str, Any],
    package_id: str,
    source_node_id: str,
) -> tuple[dict[str, Any], bool]:
    existing = _find_model(conn, source_model)
    if existing:
        _ensure_model_registry(conn, parsed, int(source_model["source_id"]), int(existing["id"]))
        target_name = _clean_text(source_model.get("name")).strip()
        known_version = _manifest_server_version(source_model)
        current_version = int(existing.get("version") or 1)
        if known_version is not None and current_version > known_version:
            return existing, False
        if target_name and target_name != existing.get("name"):
            version = current_version + 1
            conn.execute(
                db.text(
                    """UPDATE aircraft_models
                       SET name=:name,
                           source_node_id=:source_node_id,
                           version=:version,
                           updated_at=:updated_at
                       WHERE id=:id"""
                ),
                {
                    "id": int(existing["id"]),
                    "name": target_name,
                    "source_node_id": source_model.get("source_node_id") or existing.get("source_node_id"),
                    "version": version,
                    "updated_at": source_model.get("updated_at") or db.utcnow(),
                },
            )
            row = conn.execute(
                db.text("SELECT * FROM aircraft_models WHERE id=:id"),
                {"id": int(existing["id"])},
            ).first()
            updated = _row_dict(row) or existing
            _insert_change_with_type(
                conn,
                "aircraft_model",
                int(updated["id"]),
                "update",
                version,
                package_id,
                source_node_id,
            )
            return updated, False
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


def _insert_change_with_type(
    conn,
    entity_type: str,
    entity_id: int,
    change_type: str,
    version: int,
    package_id: str,
    source_node_id: str,
) -> None:
    conn.execute(
        db.text(
            """INSERT INTO sync_changes
                 (entity_type, entity_id, change_type, entity_version, changed_at,
                  changed_by_node_id, package_id)
               VALUES
                 (:entity_type, :entity_id, :change_type, :entity_version, :changed_at,
                  :changed_by_node_id, :package_id)"""
        ),
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "change_type": change_type,
            "entity_version": version,
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
        target_name = _clean_text(aircraft.get("name")).strip()
        known_version = _manifest_server_version(aircraft)
        current_version = int(existing.get("version") or 1)
        if known_version is not None and current_version > known_version:
            return existing, False
        if target_name and target_name != existing.get("name"):
            version = current_version + 1
            conn.execute(
                db.text(
                    """UPDATE aircraft
                       SET name=:name,
                           source_node_id=:source_node_id,
                           version=:version,
                           updated_at=:updated_at
                       WHERE id=:id"""
                ),
                {
                    "id": int(existing["id"]),
                    "name": target_name,
                    "source_node_id": aircraft.get("source_node_id") or source_node_id,
                    "version": version,
                    "updated_at": aircraft.get("updated_at") or db.utcnow(),
                },
            )
            row = conn.execute(
                db.text("SELECT * FROM aircraft WHERE id=:id"),
                {"id": int(existing["id"])},
            ).first()
            updated = _row_dict(row) or existing
            _insert_change_with_type(conn, "aircraft", int(updated["id"]), "update", version, package_id, source_node_id)
            return updated, False
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
    "record_total_duration_min",
    "record_location",
    "record_payload",
    "record_weather",
    "record_fuel_amount",
    "record_takeoff_weight",
    "record_altitude",
    "record_wind_speed",
    "record_wind_direction",
    "record_temperature",
    "record_note",
    "created_at",
    "updated_at",
]


def _flight_metadata_values(
    flight: dict[str, Any],
    server_aircraft_id: int,
    source_node_id: str,
) -> dict[str, Any]:
    return {
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
        "record_total_duration_min": flight.get("record_total_duration_min"),
        "record_location": _clean_text(flight.get("record_location")),
        "record_payload": _clean_text(flight.get("record_payload")),
        "record_weather": _clean_text(flight.get("record_weather")),
        "record_fuel_amount": flight.get("record_fuel_amount"),
        "record_takeoff_weight": flight.get("record_takeoff_weight"),
        "record_altitude": flight.get("record_altitude"),
        "record_wind_speed": flight.get("record_wind_speed"),
        "record_wind_direction": _clean_text(flight.get("record_wind_direction")),
        "record_temperature": flight.get("record_temperature"),
        "record_note": _clean_text(flight.get("record_note")),
    }


def _values_differ(left: Any, right: Any) -> bool:
    if left is None and right in (None, ""):
        return False
    if right is None and left in (None, ""):
        return False
    return str(left) != str(right)


def _flight_metadata_changed(existing: dict[str, Any], values: dict[str, Any]) -> bool:
    comparable = [
        "name",
        "record_total_duration_min",
        "record_location",
        "record_payload",
        "record_weather",
        "record_fuel_amount",
        "record_takeoff_weight",
        "record_altitude",
        "record_wind_speed",
        "record_wind_direction",
        "record_temperature",
        "record_note",
    ]
    return any(_values_differ(existing.get(column), values.get(column)) for column in comparable)


def _ensure_flight(
    conn,
    flight: dict[str, Any],
    server_aircraft_id: int,
    package_id: str,
    source_node_id: str,
) -> tuple[dict[str, Any], bool, bool]:
    existing = _find_flight_by_client_uid(conn, flight.get("client_uid"))
    if not existing:
        existing = _find_flight_by_business(
            conn,
            server_aircraft_id,
            flight.get("flight_date"),
            flight.get("session_key"),
        )
    if existing:
        values = _flight_metadata_values(flight, server_aircraft_id, source_node_id)
        if existing.get("deleted_at") is not None:
            now = db.utcnow()
            version = int(existing.get("version") or 1) + 1
            values = {
                **values,
                "id": int(existing["id"]),
                "client_uid": existing.get("client_uid") or values.get("client_uid"),
                "version": version,
                "updated_at": flight.get("updated_at") or now,
            }
            conn.execute(
                db.text(
                    """UPDATE flights
                       SET client_uid=:client_uid,
                           source_node_id=:source_node_id,
                           aircraft_id=:aircraft_id,
                           name=:name,
                           source_path=:source_path,
                           session_key=:session_key,
                           flight_date=:flight_date,
                           start_time=:start_time,
                           end_time=:end_time,
                           duration_sec=:duration_sec,
                           total_rows=:total_rows,
                           record_total_duration_min=:record_total_duration_min,
                           record_location=:record_location,
                           record_payload=:record_payload,
                           record_weather=:record_weather,
                           record_fuel_amount=:record_fuel_amount,
                           record_takeoff_weight=:record_takeoff_weight,
                           record_altitude=:record_altitude,
                           record_wind_speed=:record_wind_speed,
                           record_wind_direction=:record_wind_direction,
                           record_temperature=:record_temperature,
                           record_note=:record_note,
                           version=:version,
                           updated_at=:updated_at,
                           deleted_at=NULL,
                           deleted_by=NULL,
                           delete_reason=NULL
                       WHERE id=:id"""
                ),
                values,
            )
            row = conn.execute(db.text("SELECT * FROM flights WHERE id=:id"), {"id": int(existing["id"])}).first()
            restored = _row_dict(row) or {}
            _insert_change_with_type(conn, "flight", int(restored["id"]), "restore", version, package_id, source_node_id)
            return restored, False, True
        known_version = _manifest_server_version(flight)
        current_version = int(existing.get("version") or 1)
        if known_version is not None and current_version > known_version:
            return existing, False, False
        if _flight_metadata_changed(existing, values):
            now = db.utcnow()
            version = current_version + 1
            conn.execute(
                db.text(
                    """UPDATE flights
                       SET client_uid=COALESCE(client_uid, :client_uid),
                           source_node_id=:source_node_id,
                           name=:name,
                           record_total_duration_min=:record_total_duration_min,
                           record_location=:record_location,
                           record_payload=:record_payload,
                           record_weather=:record_weather,
                           record_fuel_amount=:record_fuel_amount,
                           record_takeoff_weight=:record_takeoff_weight,
                           record_altitude=:record_altitude,
                           record_wind_speed=:record_wind_speed,
                           record_wind_direction=:record_wind_direction,
                           record_temperature=:record_temperature,
                           record_note=:record_note,
                           version=:version,
                           updated_at=:updated_at
                       WHERE id=:id"""
                ),
                {
                    **values,
                    "id": int(existing["id"]),
                    "version": version,
                    "updated_at": flight.get("updated_at") or now,
                },
            )
            row = conn.execute(db.text("SELECT * FROM flights WHERE id=:id"), {"id": int(existing["id"])}).first()
            updated = _row_dict(row) or {}
            _insert_change_with_type(conn, "flight", int(updated["id"]), "update", version, package_id, source_node_id)
            return updated, False, False
        return existing, False, False
    now = db.utcnow()
    values = {
        **_flight_metadata_values(flight, server_aircraft_id, source_node_id),
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
    return created, True, False


def _server_flight_context(conn, flight_id: int) -> dict[str, Any]:
    row = conn.execute(
        db.text(
            """SELECT f.id, f.name, f.session_key, f.flight_date,
                      a.id AS aircraft_id, a.name AS aircraft_name,
                      am.id AS model_id, am.name AS model_name
               FROM flights f
               JOIN aircraft a ON a.id=f.aircraft_id
               JOIN aircraft_models am ON am.id=a.model_id
               WHERE f.id=:flight_id"""
        ),
        {"flight_id": flight_id},
    ).first()
    data = _row_dict(row)
    if not data:
        raise ValueError(f"server flight not found: {flight_id}")
    return data


def _server_storage_rel_path(conn, flight_id: int, raw: dict[str, Any]) -> str:
    flight = _server_flight_context(conn, flight_id)
    date = _date_prefix(flight.get("flight_date"))
    aircraft = f"{_safe_part(flight.get('aircraft_name'), 'aircraft')}__aircraft_{flight['aircraft_id']}"
    original = str(raw.get("original_rel_path") or raw.get("original_name") or raw.get("sha256") or "raw_file").replace("\\", "/")
    parts = [_safe_part(part) for part in original.split("/") if part and part != "."]
    if not parts:
        parts = [_safe_part(raw.get("original_name") or "raw_file")]
    if not parts[-1].startswith(f"{date}_"):
        parts[-1] = f"{date}_{parts[-1]}"
    return PurePosixPath(aircraft, *parts).as_posix()


def _unique_server_storage_rel_path(conn, desired_rel: str, flight_id: int) -> str:
    path = PurePosixPath(desired_rel)
    suffix = path.suffix
    stem = path.name[:-len(suffix)] if suffix else path.name
    parent = path.parent
    index = 0
    while True:
        name = path.name if index == 0 else f"{stem}__{index}{suffix}"
        candidate = name if str(parent) == "." else (parent / name).as_posix()
        row = conn.execute(
            db.text("SELECT id FROM flight_raw_files WHERE flight_id=:flight_id AND storage_rel_path=:path"),
            {"flight_id": flight_id, "path": candidate},
        ).first()
        if not row and not os.path.exists(_server_raw_abs_path(candidate)):
            return candidate
        index += 1


def _copy_raw_file_from_zip(
    bundle_path: str,
    package_path: str,
    storage_rel_path: str,
    expected_sha: str,
    expected_size: int,
) -> str:
    storage_rel_path = _safe_zip_path(storage_rel_path)
    destination = _server_raw_abs_path(storage_rel_path)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if os.path.exists(destination):
        if os.path.getsize(destination) != expected_size or _sha256_file(destination) != expected_sha:
            raise ValueError(f"stored raw file hash/size mismatch: {storage_rel_path}")
        return storage_rel_path

    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".object", dir=os.path.dirname(destination))
    os.close(fd)
    try:
        with zipfile.ZipFile(bundle_path, "r") as zf:
            with zf.open(package_path) as src, open(tmp_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
        if os.path.getsize(tmp_path) != expected_size:
            raise ValueError("raw file size does not match manifest")
        if _sha256_file(tmp_path) != expected_sha:
            raise ValueError("raw file sha256 does not match manifest")
        os.replace(tmp_path, destination)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return storage_rel_path


def _attach_raw_file(conn, raw: dict[str, Any], server_flight_id: int, bundle_path: str) -> tuple[int | None, bool, str | None]:
    expected_sha = str(raw.get("sha256") or "").lower()
    expected_size = _as_int(raw.get("size_bytes"))
    if not re.match(r"^[0-9a-fA-F]{64}$", expected_sha) or expected_size is None:
        return None, False, "raw file has invalid sha256 or size"

    package_path = raw.get("package_path") or f"raw_files/{raw.get('storage_rel_path') or ''}"
    package_path = _safe_zip_path(package_path)
    desired_rel = _server_storage_rel_path(conn, server_flight_id, raw)
    storage_rel_path = _unique_server_storage_rel_path(conn, desired_rel, server_flight_id)
    try:
        stored_rel = _copy_raw_file_from_zip(
            bundle_path,
            package_path,
            storage_rel_path,
            expected_sha.lower(),
            expected_size,
        )
    except Exception as exc:
        return None, False, str(exc)
    existing = conn.execute(
        db.text(
            """SELECT id FROM flight_raw_files
               WHERE flight_id=:flight_id
                 AND storage_rel_path=:storage_rel_path"""
        ),
        {
            "flight_id": server_flight_id,
            "storage_rel_path": stored_rel,
        },
    ).first()
    if existing:
        return int(existing._mapping["id"]), False, None
    conn.execute(
        db.text(
            """INSERT INTO flight_raw_files
                 (flight_id, original_name, original_rel_path, storage_rel_path,
                  sha256, size_bytes, data_type_key, source_mtime, created_at)
               VALUES
                 (:flight_id, :original_name, :original_rel_path, :storage_rel_path,
                  :sha256, :size_bytes, :data_type_key, :source_mtime, :created_at)"""
        ),
        {
            "flight_id": server_flight_id,
            "original_name": _clean_text(raw.get("original_name")),
            "original_rel_path": _clean_text(raw.get("original_rel_path")),
            "storage_rel_path": stored_rel,
            "sha256": expected_sha,
            "size_bytes": expected_size,
            "data_type_key": raw.get("data_type_key"),
            "source_mtime": raw.get("source_mtime"),
            "created_at": raw.get("created_at") or db.utcnow(),
        },
    )
    row = conn.execute(db.text("SELECT LAST_INSERT_ID() AS id")).first()
    return int(row._mapping["id"]), True, None


def _insert_dynamic_rows(
    conn,
    parsed: sqlite3.Connection,
    source_model_id: int,
    server_model_id: int,
    flight_map: dict[int, int],
) -> int:
    if not flight_map:
        return 0
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
        for server_flight_id in sorted(set(flight_map.values())):
            conn.execute(
                db.text(f"DELETE FROM {server_table_q} WHERE flight_id=:flight_id"),
                {"flight_id": server_flight_id},
            )
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
        placeholders = ", ".join(f":{col}" for col in columns)
        insert_stmt = db.text(
            f"INSERT INTO {server_table_q} "
            f"({', '.join(db.quote_identifier(c) for c in columns)}) "
            f"VALUES ({placeholders})"
        )
        cursor = parsed.execute(query)
        while rows := cursor.fetchmany(DYNAMIC_INSERT_BATCH_SIZE):
            batch = []
            for row in rows:
                source_flight_id = _as_int(row["source_flight_id"])
                server_flight_id = flight_map.get(source_flight_id or -1)
                if server_flight_id is None:
                    continue
                values = {"flight_id": server_flight_id}
                for col in columns[1:]:
                    values[col] = row[col]
                batch.append(values)
            if batch:
                conn.execute(insert_stmt, batch)
                inserted += len(batch)
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
                "mappings": {"models": [], "aircraft": [], "flights": [], "raw_files": []},
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
    refreshed_source_flights: set[int] = set()

    imported_counts = {
        "models": 0,
        "aircraft": 0,
        "flights": 0,
        "raw_links": 0,
        "dynamic_rows": 0,
        "restored_flights": 0,
    }
    existing_counts = {"models": 0, "aircraft": 0, "flights": 0, "raw_links": 0}
    warnings: list[dict[str, Any]] = []
    mappings = {"models": [], "aircraft": [], "flights": [], "raw_files": []}

    for source_model in model_rows:
        source_id = int(source_model["source_id"])
        server_model, created = _ensure_model(conn, parsed, source_model, package_id, source_node_id)
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
        server_flight, created, restored = _ensure_flight(
            conn,
            source_flight,
            int(server_aircraft["id"]),
            package_id,
            source_node_id,
        )
        flight_map[source_id] = server_flight
        if created:
            imported_counts["flights"] += 1
            refreshed_source_flights.add(source_id)
        elif restored:
            imported_counts["restored_flights"] += 1
            refreshed_source_flights.add(source_id)
        else:
            existing_counts["flights"] += 1
        mappings["flights"].append(_mapping_item(source_id, server_flight))

    for source_model_id, server_model in model_map.items():
        rows = _insert_dynamic_rows(
            conn,
            parsed,
            source_model_id,
            int(server_model["id"]),
            {source_id: int(row["id"]) for source_id, row in flight_map.items() if source_id in refreshed_source_flights},
        )
        imported_counts["dynamic_rows"] += rows

    for raw in manifest.get("raw_files") or []:
        source_flight_id = _first_manifest_id(raw, "flight_id", "source_flight_id")
        if source_flight_id is None or source_flight_id not in flight_map:
            continue
        raw_link_id, created, warning = _attach_raw_file(
            conn,
            raw,
            int(flight_map[source_flight_id]["id"]),
            bundle_path,
        )
        if warning:
            warnings.append(
                {
                    "type": "raw_file",
                    "source_flight_id": source_flight_id,
                    "sha256": raw.get("sha256"),
                    "message": warning,
                }
            )
            continue
        if not raw_link_id:
            continue
        if created:
            imported_counts["raw_links"] += 1
        else:
            existing_counts["raw_links"] += 1
        mappings["raw_files"].append(
            {
                "source_id": raw.get("id"),
                "client_uid": raw.get("client_uid"),
                "server_id": raw_link_id,
                "source_flight_id": source_flight_id,
                "server_flight_id": int(flight_map[source_flight_id]["id"]),
            }
        )

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


def list_changes(conn, since: int | str | None = None) -> dict[str, Any]:
    """Return lightweight server change rows after a global cursor."""
    since_cursor = _as_int(since) or 0
    rows = conn.execute(
        db.text(
            """SELECT `cursor`, entity_type, entity_id, change_type, entity_version,
                      changed_at, changed_by_node_id, package_id
               FROM sync_changes
               WHERE `cursor` > :since
               ORDER BY `cursor` ASC"""
        ),
        {"since": since_cursor},
    ).fetchall()
    changes = [_row_dict(row) or {} for row in rows]
    return {
        "ok": True,
        "since": since_cursor,
        "current_cursor": _max_cursor(conn),
        "count": len(changes),
        "changes": changes,
    }


def _changed_entity_ids(
    conn,
    since: int | str | None,
    *,
    exclude_source_node_id: str | None = None,
) -> dict[str, set[int]]:
    since_cursor = _as_int(since) or 0
    ids = {"models": set(), "aircraft": set(), "flights": set()}
    if since_cursor <= 0:
        for table, key in (
            ("aircraft_models", "models"),
            ("aircraft", "aircraft"),
            ("flights", "flights"),
        ):
            if exclude_source_node_id:
                rows = conn.execute(
                    db.text(f"SELECT id FROM {table} WHERE source_node_id IS NULL OR source_node_id != :exclude_source_node_id"),
                    {"exclude_source_node_id": exclude_source_node_id},
                ).fetchall()
            else:
                rows = conn.execute(db.text(f"SELECT id FROM {table}")).fetchall()
            ids[key].update(int(row._mapping["id"]) for row in rows)
        return ids

    source_filter = ""
    params: dict[str, Any] = {"since": since_cursor}
    if exclude_source_node_id:
        source_filter = " AND (changed_by_node_id IS NULL OR changed_by_node_id != :exclude_source_node_id)"
        params["exclude_source_node_id"] = exclude_source_node_id
    rows = conn.execute(
        db.text(
            """SELECT entity_type, entity_id
               FROM sync_changes
               WHERE `cursor` > :since""" + source_filter
        ),
        params,
    ).fetchall()
    for row in rows:
        entity_type = str(row._mapping["entity_type"])
        entity_id = int(row._mapping["entity_id"])
        if entity_type in {"aircraft_model", "model"}:
            ids["models"].add(entity_id)
        elif entity_type == "aircraft":
            ids["aircraft"].add(entity_id)
        elif entity_type == "flight":
            ids["flights"].add(entity_id)

    if ids["aircraft"]:
        rows = conn.execute(
            db.text(
                f"SELECT id FROM flights WHERE aircraft_id IN ({', '.join(str(i) for i in sorted(ids['aircraft']))})"
            )
        ).fetchall()
        ids["flights"].update(int(row._mapping["id"]) for row in rows)
    if ids["models"]:
        rows = conn.execute(
            db.text(
                f"SELECT id FROM aircraft WHERE model_id IN ({', '.join(str(i) for i in sorted(ids['models']))})"
            )
        ).fetchall()
        aircraft_ids = {int(row._mapping["id"]) for row in rows}
        ids["aircraft"].update(aircraft_ids)
        if aircraft_ids:
            rows = conn.execute(
                db.text(
                    f"SELECT id FROM flights WHERE aircraft_id IN ({', '.join(str(i) for i in sorted(aircraft_ids))})"
                )
            ).fetchall()
            ids["flights"].update(int(row._mapping["id"]) for row in rows)

    if ids["flights"]:
        rows = conn.execute(
            db.text(
                f"""SELECT f.aircraft_id, a.model_id
                    FROM flights f
                    JOIN aircraft a ON a.id=f.aircraft_id
                    WHERE f.id IN ({', '.join(str(i) for i in sorted(ids['flights']))})"""
            )
        ).fetchall()
        ids["aircraft"].update(int(row._mapping["aircraft_id"]) for row in rows)
        ids["models"].update(int(row._mapping["model_id"]) for row in rows)
    if ids["aircraft"]:
        rows = conn.execute(
            db.text(
                f"SELECT model_id FROM aircraft WHERE id IN ({', '.join(str(i) for i in sorted(ids['aircraft']))})"
            )
        ).fetchall()
        ids["models"].update(int(row._mapping["model_id"]) for row in rows)
    return ids


def _select_rows_by_ids(conn, table: str, ids: set[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ", ".join(f":id{i}" for i, _ in enumerate(ids))
    params = {f"id{i}": value for i, value in enumerate(sorted(ids))}
    rows = conn.execute(
        db.text(f"SELECT * FROM {table} WHERE id IN ({placeholders}) ORDER BY id"),
        params,
    ).fetchall()
    return [_row_dict(row) or {} for row in rows]


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _write_source_table(
    sqlite_conn: sqlite3.Connection,
    table: str,
    rows: list[dict[str, Any]],
    source_fk_map: dict[str, str] | None = None,
) -> None:
    source_fk_map = source_fk_map or {}
    if not rows:
        return
    keys = list(rows[0].keys())
    cols = ["source_id INTEGER"]
    for key in keys:
        if key == "id":
            continue
        out_key = source_fk_map.get(key, key)
        sample = next((row.get(key) for row in rows if row.get(key) is not None), None)
        if isinstance(sample, int):
            col_type = "INTEGER"
        elif isinstance(sample, float):
            col_type = "REAL"
        else:
            col_type = "TEXT"
        cols.append(f"{_q_sqlite(out_key)} {col_type}")
    sqlite_conn.execute(f"CREATE TABLE {_q_sqlite(table)} ({', '.join(cols)})")
    insert_cols = ["source_id"] + [source_fk_map.get(key, key) for key in keys if key != "id"]
    placeholders = ",".join("?" for _ in insert_cols)
    sqlite_conn.executemany(
        f"INSERT INTO {_q_sqlite(table)} ({', '.join(_q_sqlite(c) for c in insert_cols)}) VALUES ({placeholders})",
        [
            [_sqlite_value(row.get("id"))] + [_sqlite_value(row.get(key)) for key in keys if key != "id"]
            for row in rows
        ],
    )


def _server_model_config(conn, server_model_id: int) -> dict[str, Any]:
    model = _row_dict(
        conn.execute(
            db.text(
                """SELECT has_header, has_uav_send_id, extract_serial_from_path
                   FROM aircraft_models WHERE id=:id"""
            ),
            {"id": server_model_id},
        ).first()
    ) or {}
    config = {
        "has_header": bool(model.get("has_header", 1)),
        "has_uav_send_id": bool(model.get("has_uav_send_id", 0)),
        "extract_serial_from_path": bool(model.get("extract_serial_from_path", 0)),
        "data_types": {},
    }
    dtr_rows = conn.execute(
        db.text(
            """SELECT data_type_key, display_label, file_patterns, is_alert
               FROM data_table_registry
               WHERE model_id=:model_id
               ORDER BY data_type_key"""
        ),
        {"model_id": server_model_id},
    ).fetchall()
    for dtr_row in dtr_rows:
        dtr = _row_dict(dtr_row) or {}
        data_type_key = str(dtr["data_type_key"])
        try:
            patterns = json.loads(dtr.get("file_patterns") or "[]")
        except Exception:
            patterns = []
        col_rows = conn.execute(
            db.text(
                """SELECT column_name, display_label, unit, data_type, ordinal, scale_factor
                   FROM column_registry
                   WHERE model_id=:model_id AND data_type_key=:data_type_key
                   ORDER BY ordinal"""
            ),
            {"model_id": server_model_id, "data_type_key": data_type_key},
        ).fetchall()
        config["data_types"][data_type_key] = {
            "display_label": dtr.get("display_label") or data_type_key,
            "file_patterns": patterns if isinstance(patterns, list) else [],
            "is_alert": bool(dtr.get("is_alert")),
            "columns": [
                {
                    "name": (_row_dict(col) or {}).get("column_name"),
                    "label": (_row_dict(col) or {}).get("display_label"),
                    "unit": (_row_dict(col) or {}).get("unit") or "",
                    "type": (_row_dict(col) or {}).get("data_type") or "REAL",
                    "ordinal": (_row_dict(col) or {}).get("ordinal"),
                    "scale_factor": (_row_dict(col) or {}).get("scale_factor") or 1.0,
                }
                for col in col_rows
            ],
        }
    return config


def _write_server_parsed_sqlite(conn, ids: dict[str, set[int]], out_path: str) -> int:
    if os.path.exists(out_path):
        os.remove(out_path)
    sqlite_conn = sqlite3.connect(out_path)
    try:
        model_rows = _select_rows_by_ids(conn, "aircraft_models", ids["models"])
        aircraft_rows = _select_rows_by_ids(conn, "aircraft", ids["aircraft"])
        flight_rows = _select_rows_by_ids(conn, "flights", ids["flights"])
        _write_source_table(sqlite_conn, "aircraft_models", model_rows)
        _write_source_table(sqlite_conn, "aircraft", aircraft_rows, {"model_id": "source_model_id"})
        _write_source_table(sqlite_conn, "flights", flight_rows, {"aircraft_id": "source_aircraft_id"})

        dtr_rows: list[dict[str, Any]] = []
        col_rows: list[dict[str, Any]] = []
        for model_id in sorted(ids["models"]):
            dtr_rows.extend(_select_rows_by_ids_for_model(conn, "data_table_registry", model_id))
            col_rows.extend(_select_rows_by_ids_for_model(conn, "column_registry", model_id))
        _write_source_table(sqlite_conn, "data_table_registry", dtr_rows, {"model_id": "source_model_id"})
        _write_source_table(sqlite_conn, "column_registry", col_rows, {"model_id": "source_model_id"})

        parsed_rows = 0
        for dtr in dtr_rows:
            server_table = str(dtr.get("table_name") or "")
            server_table_q = db.quote_identifier(server_table)
            source_table_q = _q_sqlite(server_table)
            pragma_rows = conn.execute(db.text(f"SHOW COLUMNS FROM {server_table_q}")).fetchall()
            source_cols = [row._mapping["Field"] for row in pragma_rows]
            out_cols = ["source_id INTEGER"]
            for col in source_cols:
                if col == "id":
                    continue
                out_name = "source_flight_id" if col == "flight_id" else col
                out_cols.append(f"{_q_sqlite(out_name)} TEXT")
            sqlite_conn.execute(f"CREATE TABLE {source_table_q} ({', '.join(out_cols)})")
            model_flight_ids = sorted(ids["flights"])
            if not model_flight_ids:
                continue
            placeholders = ", ".join(f":fid{i}" for i, _ in enumerate(model_flight_ids))
            params = {f"fid{i}": fid for i, fid in enumerate(model_flight_ids)}
            rows = conn.execute(
                db.text(
                    f"SELECT * FROM {server_table_q} WHERE flight_id IN ({placeholders}) ORDER BY flight_id, id"
                ),
                params,
            ).fetchall()
            if not rows:
                continue
            insert_cols = ["source_id"] + [
                ("source_flight_id" if col == "flight_id" else col)
                for col in source_cols
                if col != "id"
            ]
            sqlite_conn.executemany(
                f"INSERT INTO {source_table_q} ({', '.join(_q_sqlite(c) for c in insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})",
                [
                    [_sqlite_value(row._mapping.get("id"))]
                    + [_sqlite_value(row._mapping.get(col)) for col in source_cols if col != "id"]
                    for row in rows
                ],
            )
            parsed_rows += len(rows)
        sqlite_conn.commit()
        return parsed_rows
    finally:
        sqlite_conn.close()


def _select_rows_by_ids_for_model(conn, table: str, model_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        db.text(f"SELECT * FROM {table} WHERE model_id=:model_id ORDER BY id"),
        {"model_id": model_id},
    ).fetchall()
    return [_row_dict(row) or {} for row in rows]


def build_pull_preview(
    conn,
    since: int | str | None = None,
    *,
    exclude_source_node_id: str | None = None,
) -> dict[str, Any]:
    """Return a lightweight pull preview without creating or downloading a bundle."""
    ids = _changed_entity_ids(conn, since, exclude_source_node_id=exclude_source_node_id)
    current_cursor = _max_cursor(conn)
    model_rows = _select_rows_by_ids(conn, "aircraft_models", ids["models"])
    aircraft_rows = _select_rows_by_ids(conn, "aircraft", ids["aircraft"])
    flight_rows = _select_rows_by_ids(conn, "flights", ids["flights"])
    raw_rows = _server_raw_manifest_rows(conn, ids["flights"])
    return {
        "ok": True,
        "bundle_kind": "pull_preview",
        "source_node_id": "server",
        "source_environment": "server",
        "base_server_cursor": str(since or ""),
        "server_cursor": current_cursor,
        "models": model_rows,
        "aircraft": aircraft_rows,
        "flights": flight_rows,
        "raw_files": raw_rows,
        "summary": {
            "models": len(model_rows),
            "aircraft": len(aircraft_rows),
            "flights": len(flight_rows),
            "raw_files": len(raw_rows),
        },
    }


def build_pull_bundle(
    conn,
    since: int | str | None = None,
    *,
    exclude_source_node_id: str | None = None,
) -> dict[str, Any]:
    """Create a pull_bundle zip from server state and return its path."""
    ids = _changed_entity_ids(conn, since, exclude_source_node_id=exclude_source_node_id)
    current_cursor = _max_cursor(conn)
    package_id = f"pkg-{uuid.uuid4().hex}"
    source_node_id = "server"
    tmp_dir = tempfile.mkdtemp(prefix="flightanalyzer_server_pull_")
    try:
        parsed_path = os.path.join(tmp_dir, "parsed.sqlite")
        parsed_rows = _write_server_parsed_sqlite(conn, ids, parsed_path)
        parsed_sha = _sha256_file(parsed_path)

        model_rows = _select_rows_by_ids(conn, "aircraft_models", ids["models"])
        aircraft_rows = _select_rows_by_ids(conn, "aircraft", ids["aircraft"])
        flight_rows = _select_rows_by_ids(conn, "flights", ids["flights"])
        raw_rows = _server_raw_manifest_rows(conn, ids["flights"])
        manifest = {
            "package_version": 2,
            "sync_protocol_version": 1,
            "package_id": package_id,
            "bundle_kind": "pull_bundle",
            "app_version": "2.0.0",
            "schema_version": 2,
            "source_node_id": source_node_id,
            "source_environment": "server",
            "exported_at": db.utcnow().isoformat(timespec="seconds"),
            "base_server_cursor": str(since or ""),
            "server_cursor": current_cursor,
            "models": model_rows,
            "aircraft": aircraft_rows,
            "flights": flight_rows,
            "raw_files": raw_rows,
            "parsed_data": {
                "format": "sqlite",
                "path": "data/parsed.sqlite",
                "sha256": parsed_sha,
                "size_bytes": os.path.getsize(parsed_path),
            },
        }

        bundles_dir = os.path.join(db.SERVER_DATA_DIR, "bundles")
        os.makedirs(bundles_dir, exist_ok=True)
        bundle_path = os.path.join(bundles_dir, f"server_pull_{current_cursor}_{package_id}.fapkg")
        manifest_path = os.path.join(tmp_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, default=_json_default)
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(manifest_path, "manifest.json")
            zf.write(parsed_path, "data/parsed.sqlite")
            for model_id in sorted(ids["models"]):
                zf.writestr(
                    f"models/model_{model_id}.json",
                    json.dumps(_server_model_config(conn, model_id), ensure_ascii=False, indent=2, default=_json_default),
                )
            for raw in raw_rows:
                src = _server_raw_abs_path(raw["storage_rel_path"])
                root = os.path.abspath(os.path.join(db.SERVER_DATA_DIR, "raw_files"))
                if os.path.commonpath([src, root]) != root or not os.path.exists(src):
                    continue
                zf.write(src, _safe_zip_path(raw["package_path"]))
        return {
            "ok": True,
            "path": bundle_path,
            "package_id": package_id,
            "current_cursor": current_cursor,
            "since": _as_int(since) or 0,
            "summary": {
                "models": len(model_rows),
                "aircraft": len(aircraft_rows),
                "flights": len(flight_rows),
                "raw_files": len(raw_rows),
                "parsed_rows": parsed_rows,
            },
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _server_raw_manifest_rows(conn, flight_ids: set[int]) -> list[dict[str, Any]]:
    if not flight_ids:
        return []
    placeholders = ", ".join(f":fid{i}" for i, _ in enumerate(flight_ids))
    params = {f"fid{i}": fid for i, fid in enumerate(sorted(flight_ids))}
    rows = conn.execute(
        db.text(
            f"""SELECT frf.id, frf.flight_id, frf.original_name, frf.original_rel_path,
                      frf.storage_rel_path, frf.sha256, frf.size_bytes,
                      frf.data_type_key, frf.source_mtime, frf.created_at
               FROM flight_raw_files frf
               WHERE frf.flight_id IN ({placeholders})
               ORDER BY frf.flight_id, frf.original_rel_path, frf.id"""
        ),
        params,
    ).fetchall()
    raw_files = []
    for row in rows:
        item = _row_dict(row) or {}
        storage_rel_path = _safe_zip_path(item["storage_rel_path"])
        item["storage_rel_path"] = storage_rel_path
        item["package_path"] = _safe_zip_path(f"raw_files/{storage_rel_path}")
        raw_files.append(item)
    return raw_files


def soft_delete_entity(
    conn,
    entity_type: str,
    entity_id: int,
    *,
    deleted_by: int | None,
    reason: str | None = None,
) -> dict[str, Any]:
    if entity_type not in {"model", "aircraft", "flight"}:
        raise ValueError(f"Unsupported delete entity type: {entity_type}")
    table = {"model": "aircraft_models", "aircraft": "aircraft", "flight": "flights"}[entity_type]
    row = conn.execute(db.text(f"SELECT id, version, deleted_at FROM {table} WHERE id=:id"), {"id": entity_id}).first()
    if not row:
        raise KeyError(entity_type)
    now = db.utcnow()
    affected: list[dict[str, Any]] = []

    def mark_deleted(target_type: str, target_table: str, target_id: int, *, with_reason: bool = False) -> int | None:
        target = conn.execute(
            db.text(f"SELECT id, version, deleted_at FROM {target_table} WHERE id=:id"),
            {"id": target_id},
        ).first()
        if not target:
            return None
        if target._mapping.get("deleted_at") is not None:
            version_value = int(target._mapping.get("version") or 1)
            affected.append(
                {
                    "entity_type": target_type,
                    "id": target_id,
                    "version": version_value,
                    "already_deleted": True,
                }
            )
            return version_value
        version_value = int(target._mapping.get("version") or 1) + 1
        if with_reason:
            conn.execute(
                db.text(
                    """UPDATE flights
                       SET deleted_at=:deleted_at, deleted_by=:deleted_by, delete_reason=:reason,
                           version=:version, updated_at=:updated_at
                       WHERE id=:id"""
                ),
                {
                    "deleted_at": now,
                    "deleted_by": deleted_by,
                    "reason": reason or "",
                    "version": version_value,
                    "updated_at": now,
                    "id": target_id,
                },
            )
        else:
            conn.execute(
                db.text(
                    f"""UPDATE {target_table}
                        SET deleted_at=:deleted_at, version=:version, updated_at=:updated_at
                        WHERE id=:id"""
                ),
                {"deleted_at": now, "version": version_value, "updated_at": now, "id": target_id},
            )
        conn.execute(
            db.text(
                """INSERT INTO sync_changes
                     (entity_type, entity_id, change_type, entity_version, changed_at,
                      changed_by_node_id, package_id)
                   VALUES (:entity_type, :entity_id, 'delete', :entity_version,
                           :changed_at, NULL, NULL)"""
            ),
            {
                "entity_type": "aircraft_model" if target_type == "model" else target_type,
                "entity_id": target_id,
                "entity_version": version_value,
                "changed_at": now,
            },
        )
        affected.append(
            {
                "entity_type": target_type,
                "id": target_id,
                "version": version_value,
                "already_deleted": False,
            }
        )
        return version_value

    version = mark_deleted(entity_type, table, entity_id, with_reason=entity_type == "flight")
    if entity_type == "model":
        aircraft_rows = conn.execute(
            db.text("SELECT id FROM aircraft WHERE model_id=:model_id"),
            {"model_id": entity_id},
        ).fetchall()
        aircraft_ids = [int(item._mapping["id"]) for item in aircraft_rows]
        for aircraft_id in aircraft_ids:
            mark_deleted("aircraft", "aircraft", aircraft_id)
        if aircraft_ids:
            placeholders = ", ".join(f":aid{i}" for i, _ in enumerate(aircraft_ids))
            params = {f"aid{i}": aid for i, aid in enumerate(aircraft_ids)}
            flight_rows = conn.execute(
                db.text(f"SELECT id FROM flights WHERE aircraft_id IN ({placeholders})"),
                params,
            ).fetchall()
            for flight_row in flight_rows:
                mark_deleted("flight", "flights", int(flight_row._mapping["id"]), with_reason=True)
    elif entity_type == "aircraft":
        flight_rows = conn.execute(
            db.text("SELECT id FROM flights WHERE aircraft_id=:aircraft_id"),
            {"aircraft_id": entity_id},
        ).fetchall()
        for flight_row in flight_rows:
            mark_deleted("flight", "flights", int(flight_row._mapping["id"]), with_reason=True)

    return {
        "ok": True,
        "entity_type": entity_type,
        "id": entity_id,
        "version": version,
        "deleted_at": now,
        "affected": affected,
    }
