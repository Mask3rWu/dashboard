"""Server-side preflight and push import for sync bundles."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile
from collections import defaultdict
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

from backend import server_database as db
from .metrics import SyncMetrics
from . import server_operations
from .cleanup import cleanup_files
from . import upload_sessions
from .protocol import (
    CURRENT_SCHEMA_VERSION,
    model_mutable_metadata_payload,
    model_structure_signature,
    safe_zip_path as _safe_zip_path,
    sha256_file as _sha256_file,
    validate_server_manifest,
)


WINDOWS_INVALID_CHARS = set('<>:"\\|?*')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
DYNAMIC_INSERT_MIN_ROWS = 100
DYNAMIC_INSERT_MAX_ROWS = 1000
DYNAMIC_INSERT_TARGET_CELLS = 10_000
SERVER_ID_QUERY_BATCH_SIZE = 500
ENTITY_TABLES = {
    "model": "aircraft_models",
    "aircraft": "aircraft",
    "flight": "flights",
}


def _batched_ids(values, batch_size: int = SERVER_ID_QUERY_BATCH_SIZE):
    clean = sorted({int(value) for value in values})
    for start in range(0, len(clean), batch_size):
        yield clean[start:start + batch_size]


def _dynamic_insert_batch_size(column_count: int) -> int:
    by_width = DYNAMIC_INSERT_TARGET_CELLS // max(1, int(column_count))
    return max(DYNAMIC_INSERT_MIN_ROWS, min(DYNAMIC_INSERT_MAX_ROWS, by_width))


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
    return validate_server_manifest(manifest, require_push_batch=require_push_batch)


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


def _find_entity_by_mapping(
    conn,
    client_node_id: str | None,
    entity_type: str,
    client_entity_uid: str | None,
) -> dict[str, Any] | None:
    table = ENTITY_TABLES.get(entity_type)
    if not table:
        raise ValueError(f"Unsupported sync entity type: {entity_type}")
    node_id = _clean_text(client_node_id).strip()
    client_uid = _clean_text(client_entity_uid).strip()
    if not node_id or not client_uid:
        return None
    row = conn.execute(
        db.text(
            f"""SELECT entity.*
                FROM sync_entity_mappings mapping
                JOIN {table} entity ON entity.id=mapping.server_entity_id
                WHERE mapping.client_node_id=:client_node_id
                  AND mapping.entity_type=:entity_type
                  AND mapping.client_entity_uid=:client_entity_uid"""
        ),
        {
            "client_node_id": node_id,
            "entity_type": entity_type,
            "client_entity_uid": client_uid,
        },
    ).first()
    return _row_dict(row)


def _record_entity_mapping(
    conn,
    client_node_id: str,
    entity_type: str,
    client_entity_uid: str | None,
    server_entity_id: int,
    matched_by: str,
) -> None:
    if entity_type not in ENTITY_TABLES:
        raise ValueError(f"Unsupported sync entity type: {entity_type}")
    node_id = _clean_text(client_node_id).strip()
    client_uid = _clean_text(client_entity_uid).strip()
    if not node_id or not client_uid:
        return
    now = db.utcnow()
    params = {
        "client_node_id": node_id,
        "entity_type": entity_type,
        "client_entity_uid": client_uid,
        "server_entity_id": int(server_entity_id),
        "matched_by": _clean_text(matched_by, "unknown")[:32],
        "created_at": now,
        "updated_at": now,
    }
    conn.execute(
        db.text(
            """INSERT INTO sync_entity_mappings
                 (client_node_id, entity_type, client_entity_uid, server_entity_id,
                  matched_by, created_at, updated_at)
               VALUES
                 (:client_node_id, :entity_type, :client_entity_uid, :server_entity_id,
                  :matched_by, :created_at, :updated_at)
               ON DUPLICATE KEY UPDATE updated_at=VALUES(updated_at)"""
        ),
        params,
    )
    row = conn.execute(
        db.text(
            """SELECT server_entity_id
               FROM sync_entity_mappings
               WHERE client_node_id=:client_node_id
                 AND entity_type=:entity_type
                 AND client_entity_uid=:client_entity_uid"""
        ),
        params,
    ).first()
    mapped_id = _as_int(row._mapping.get("server_entity_id") if row else None)
    if mapped_id != int(server_entity_id):
        raise ValueError(
            f"Sync identity mapping conflict for {entity_type} "
            f"{node_id}/{client_uid}: mapped to {mapped_id}, attempted {server_entity_id}"
        )


def _entity_redirect_target(conn, entity_type: str, entity_id: int) -> int:
    if entity_type not in ENTITY_TABLES:
        raise ValueError(f"Unsupported sync entity type: {entity_type}")
    current = int(entity_id)
    visited: set[int] = set()
    while current not in visited:
        visited.add(current)
        row = conn.execute(
            db.text(
                """SELECT target_server_entity_id
                   FROM sync_entity_redirects
                   WHERE entity_type=:entity_type
                     AND source_server_entity_id=:source_id"""
            ),
            {"entity_type": entity_type, "source_id": current},
        ).first()
        if not row:
            return current
        current = int(row._mapping["target_server_entity_id"])
    raise ValueError(f"Redirect cycle detected for {entity_type} {entity_id}")


def _record_entity_redirect(
    conn,
    entity_type: str,
    source_id: int,
    target_id: int,
    *,
    created_by: int | None,
    reason: str,
) -> int:
    if entity_type not in ENTITY_TABLES:
        raise ValueError(f"Unsupported sync entity type: {entity_type}")
    source_id = int(source_id)
    target_id = _entity_redirect_target(conn, entity_type, int(target_id))
    if source_id == target_id:
        raise ValueError("Merge source and target must be different")
    now = db.utcnow()
    change_entity_type = "aircraft_model" if entity_type == "model" else entity_type
    source_table = ENTITY_TABLES[entity_type]
    source_row = conn.execute(
        db.text(f"SELECT version FROM {source_table} WHERE id=:source_id"),
        {"source_id": source_id},
    ).first()
    source_version = int(source_row._mapping.get("version") or 1) if source_row else 1
    conn.execute(
        db.text(
            """INSERT INTO sync_changes
                 (entity_type, entity_id, change_type, entity_version, changed_at,
                  changed_by_node_id, package_id)
               VALUES
                 (:entity_type, :entity_id, 'merge', :entity_version, :changed_at,
                  NULL, NULL)"""
        ),
        {
            "entity_type": change_entity_type,
            "entity_id": source_id,
            "entity_version": source_version,
            "changed_at": now,
        },
    )
    cursor_row = conn.execute(db.text("SELECT LAST_INSERT_ID() AS id")).first()
    change_cursor = int(cursor_row._mapping["id"])
    params = {
        "entity_type": entity_type,
        "source_id": source_id,
        "target_id": target_id,
        "change_cursor": change_cursor,
        "created_by": created_by,
        "reason": reason,
        "created_at": now,
        "updated_at": now,
    }
    conn.execute(
        db.text(
            """UPDATE sync_entity_redirects
               SET target_server_entity_id=:target_id,
                   change_cursor=:change_cursor,
                   updated_at=:updated_at
               WHERE entity_type=:entity_type
                 AND target_server_entity_id=:source_id"""
        ),
        params,
    )
    conn.execute(
        db.text(
            """INSERT INTO sync_entity_redirects
                 (entity_type, source_server_entity_id, target_server_entity_id,
                  change_cursor, created_by, reason, created_at, updated_at)
               VALUES
                 (:entity_type, :source_id, :target_id, :change_cursor,
                  :created_by, :reason, :created_at, :updated_at)
               ON DUPLICATE KEY UPDATE
                   target_server_entity_id=VALUES(target_server_entity_id),
                   change_cursor=VALUES(change_cursor),
                   created_by=VALUES(created_by),
                   reason=VALUES(reason),
                   updated_at=VALUES(updated_at)"""
        ),
        params,
    )
    conn.execute(
        db.text(
            """UPDATE sync_entity_mappings
               SET server_entity_id=:target_id, matched_by='merge', updated_at=:updated_at
               WHERE entity_type=:entity_type AND server_entity_id=:source_id"""
        ),
        params,
    )
    return change_cursor


def _entity_redirects_since(conn, since: int | str | None) -> list[dict[str, Any]]:
    rows = conn.execute(
        db.text(
            """SELECT entity_type, source_server_entity_id, target_server_entity_id,
                      change_cursor, reason, created_at, updated_at
               FROM sync_entity_redirects
               WHERE change_cursor > :since
               ORDER BY change_cursor, id"""
        ),
        {"since": _as_int(since) or 0},
    ).fetchall()
    return [_row_dict(row) or {} for row in rows]


def _entity_by_known_server_id(
    conn,
    entity_type: str,
    server_id: Any,
) -> dict[str, Any] | None:
    entity_id = _as_int(server_id)
    if entity_id is None:
        return None
    target_id = _entity_redirect_target(conn, entity_type, entity_id)
    table = ENTITY_TABLES[entity_type]
    row = conn.execute(
        db.text(f"SELECT * FROM {table} WHERE id=:id"),
        {"id": target_id},
    ).first()
    return _row_dict(row)


def _mapping_matches_known_server_id(
    conn,
    entity_type: str,
    mapped: dict[str, Any],
    known_server_id: Any,
) -> bool:
    if _as_int(known_server_id) is None:
        return True
    known = _entity_by_known_server_id(conn, entity_type, known_server_id)
    return bool(known and int(known["id"]) == int(mapped["id"]))


def _find_model_match(
    conn,
    model: dict[str, Any],
    source_node_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    client_uid = model.get("client_uid")
    mapped = _find_entity_by_mapping(conn, source_node_id, "model", client_uid)
    if mapped:
        if not _mapping_matches_known_server_id(conn, "model", mapped, model.get("server_id")):
            return mapped, "mapping_server_id_conflict"
        return mapped, "entity_mapping"
    known = _entity_by_known_server_id(conn, "model", model.get("server_id"))
    if known:
        return known, "server_id"
    if client_uid:
        row = conn.execute(
            db.text("SELECT * FROM aircraft_models WHERE client_uid=:client_uid"),
            {"client_uid": client_uid},
        ).first()
        if row:
            candidate = _row_dict(row) or {}
            resolved = _entity_by_known_server_id(conn, "model", candidate.get("id"))
            return resolved or candidate, "client_uid"
    signature = _model_signature_from_manifest(model)
    if signature:
        rows = conn.execute(
            db.text(
                "SELECT * FROM aircraft_models "
                "WHERE config_signature=:config_signature AND deleted_at IS NULL"
            ),
            {"config_signature": signature},
        ).fetchall()
        if len(rows) == 1:
            return _row_dict(rows[0]), "structure_signature"
        if len(rows) > 1:
            return None, "structure_ambiguous"
    name = _clean_text(model.get("name")).strip()
    if name:
        row = conn.execute(
            db.text("SELECT * FROM aircraft_models WHERE name=:name AND deleted_at IS NULL"),
            {"name": name},
        ).first()
        if row:
            return _row_dict(row), "name"
    return None, None


def _find_model(
    conn,
    model: dict[str, Any],
    source_node_id: str | None = None,
) -> dict[str, Any] | None:
    return _find_model_match(conn, model, source_node_id)[0]


def _find_model_by_name(conn, name: str) -> dict[str, Any] | None:
    row = conn.execute(
        db.text("SELECT * FROM aircraft_models WHERE name=:name AND deleted_at IS NULL"),
        {"name": _clean_text(name).strip()},
    ).first()
    return _row_dict(row)


def _find_aircraft_identity_match(
    conn,
    aircraft: dict[str, Any],
    source_node_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    client_uid = aircraft.get("client_uid")
    mapped = _find_entity_by_mapping(conn, source_node_id, "aircraft", client_uid)
    if mapped:
        if not _mapping_matches_known_server_id(
            conn, "aircraft", mapped, aircraft.get("server_id")
        ):
            return mapped, "mapping_server_id_conflict"
        return mapped, "entity_mapping"
    known = _entity_by_known_server_id(conn, "aircraft", aircraft.get("server_id"))
    if known:
        return known, "server_id"
    if client_uid:
        row = conn.execute(
            db.text("SELECT * FROM aircraft WHERE client_uid=:client_uid"),
            {"client_uid": client_uid},
        ).first()
        if row:
            candidate = _row_dict(row) or {}
            resolved = _entity_by_known_server_id(conn, "aircraft", candidate.get("id"))
            return resolved or candidate, "client_uid"
    return None, None


def _find_aircraft_match(
    conn,
    aircraft: dict[str, Any],
    server_model_id: int | None = None,
    source_node_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    existing, matched_by = _find_aircraft_identity_match(conn, aircraft, source_node_id)
    if existing:
        return existing, matched_by
    if server_model_id is None:
        return None, None
    name = _clean_text(aircraft.get("name")).strip()
    if name:
        row = conn.execute(
            db.text(
                "SELECT * FROM aircraft "
                "WHERE model_id=:model_id AND name=:name AND deleted_at IS NULL"
            ),
            {"model_id": server_model_id, "name": name},
        ).first()
        if row:
            return _row_dict(row), "name"
    return None, None


def _find_aircraft(
    conn,
    aircraft: dict[str, Any],
    server_model_id: int | None = None,
    source_node_id: str | None = None,
) -> dict[str, Any] | None:
    return _find_aircraft_match(conn, aircraft, server_model_id, source_node_id)[0]


def _find_aircraft_by_name(conn, server_model_id: int, name: str) -> dict[str, Any] | None:
    row = conn.execute(
        db.text(
            "SELECT * FROM aircraft "
            "WHERE model_id=:model_id AND name=:name AND deleted_at IS NULL"
        ),
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
    candidate = _row_dict(row)
    if not candidate:
        return None
    return _entity_by_known_server_id(conn, "flight", candidate.get("id")) or candidate


def _find_aircraft_by_id(conn, aircraft_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        db.text("SELECT * FROM aircraft WHERE id=:aircraft_id"),
        {"aircraft_id": int(aircraft_id)},
    ).first()
    return _row_dict(row)


def _find_flight_identity(
    conn,
    flight: dict[str, Any],
    source_node_id: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    mapped = _find_entity_by_mapping(conn, source_node_id, "flight", flight.get("client_uid"))
    if mapped:
        if not _mapping_matches_known_server_id(
            conn, "flight", mapped, flight.get("server_id")
        ):
            return mapped, "mapping_server_id_conflict"
        return mapped, "entity_mapping"
    known = _entity_by_known_server_id(conn, "flight", flight.get("server_id"))
    if known:
        return known, "server_id"
    existing = _find_flight_by_client_uid(conn, flight.get("client_uid"))
    return (existing, "client_uid") if existing else (None, None)


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


def _server_flight_content_identity(conn, flight_id: int) -> tuple[tuple[str, str], ...]:
    rows = conn.execute(
        db.text(
            """SELECT data_type_key, sha256
               FROM flight_raw_files
               WHERE flight_id=:flight_id
               ORDER BY COALESCE(data_type_key, ''), sha256, id"""
        ),
        {"flight_id": flight_id},
    ).fetchall()
    return tuple(
        sorted(
            (
                _clean_text(row._mapping.get("data_type_key")),
                str(row._mapping["sha256"]),
            )
            for row in rows
            if row._mapping.get("sha256")
        )
    )


def _find_flights_by_content_identity(
    conn,
    content_identity: tuple[tuple[str, str], ...],
) -> list[dict[str, Any]]:
    if not content_identity:
        return []
    candidate_sha = content_identity[0][1]
    candidate_rows = conn.execute(
        db.text(
            """SELECT DISTINCT f.*
                FROM flights f
                JOIN flight_raw_files raw ON raw.flight_id=f.id
                WHERE raw.sha256=:candidate_sha
                  AND f.deleted_at IS NULL"""
        ),
        {"candidate_sha": candidate_sha},
    ).fetchall()
    matches = []
    for candidate_row in candidate_rows:
        candidate = _row_dict(candidate_row) or {}
        if _server_flight_content_identity(conn, int(candidate["id"])) == content_identity:
            matches.append(candidate)
    return matches


def _overlap_aircraft_ids(
    conn,
    manifest: dict[str, Any],
    source_aircraft_id: int,
    content_identity_by_flight: dict[int, tuple[tuple[str, str], ...]],
) -> set[int]:
    target_aircraft_ids: set[int] = set()
    for flight in manifest.get("flights") or []:
        if _first_manifest_id(flight, "aircraft_id", "source_aircraft_id") != source_aircraft_id:
            continue
        source_flight_id = _first_manifest_id(flight, "id", "source_id")
        if source_flight_id is None:
            continue
        for match in _find_flights_by_content_identity(
            conn, content_identity_by_flight.get(source_flight_id, ())
        ):
            aircraft_id = _as_int(match.get("aircraft_id"))
            if aircraft_id is not None:
                target_aircraft_ids.add(aircraft_id)
    return target_aircraft_ids


def _manifest_server_version(row: dict[str, Any]) -> int | None:
    value = row.get("server_version")
    if value in (None, ""):
        value = row.get("version")
    return _as_int(value)


def _manifest_content_identity_by_flight(
    manifest: dict[str, Any],
) -> dict[int, tuple[tuple[str, str], ...]]:
    grouped: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for raw in manifest.get("raw_files") or []:
        source_flight_id = _first_manifest_id(raw, "flight_id", "source_flight_id")
        sha = raw.get("sha256")
        if source_flight_id is not None and sha:
            grouped[source_flight_id].append(
                (_clean_text(raw.get("data_type_key")), str(sha))
            )
    return {flight_id: tuple(sorted(values)) for flight_id, values in grouped.items()}


def _mapping_item(
    source_id: int | None,
    row: dict[str, Any],
    source_client_uid: str | None = None,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "client_uid": source_client_uid if source_client_uid is not None else row.get("client_uid"),
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
    content_identity_by_flight = _manifest_content_identity_by_flight(manifest)

    model_by_source: dict[int, dict[str, Any]] = {}
    model_plan: list[dict[str, Any]] = []
    for model in manifest.get("models") or []:
        source_id = _first_manifest_id(model, "id", "source_id")
        existing, matched_by = _find_model_match(conn, model, source_node_id)
        action = "create"
        conflict_reason = None
        if matched_by == "structure_ambiguous":
            action = "conflict"
            conflict_reason = "model_structure_ambiguous"
        elif matched_by == "mapping_server_id_conflict":
            action = "conflict"
            conflict_reason = "mapping_server_id_mismatch"
        if existing:
            known_version = _manifest_server_version(model)
            current_version = _as_int(existing.get("version")) or 1
            target_name = _clean_text(model.get("name")).strip()
            signature = _model_signature_from_manifest(model)
            if matched_by == "mapping_server_id_conflict":
                pass
            elif (
                signature
                and existing.get("config_signature")
                and signature != existing.get("config_signature")
            ):
                action = "conflict"
                conflict_reason = "model_config_mismatch"
            else:
                name_owner = _find_model_by_name(conn, target_name) if target_name else None
                if name_owner and int(name_owner["id"]) != int(existing["id"]):
                    action = "conflict"
                    conflict_reason = "model_name_conflict"
                elif (
                    (target_name and target_name != existing.get("name"))
                    or (
                        model.get("config")
                        and model_mutable_metadata_payload(model.get("config"))
                        != model_mutable_metadata_payload(_server_model_config(conn, int(existing["id"])))
                    )
                ):
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
            "server_newer": bool(
                existing
                and _manifest_server_version(model) is not None
                and (_as_int(existing.get("version")) or 1) > int(_manifest_server_version(model) or 0)
            ),
            "matched_by": matched_by,
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
        server_model_id = _as_int(server_model.get("id")) if server_model else None
        existing, matched_by = _find_aircraft_identity_match(
            conn, aircraft, source_node_id
        )
        identity_conflict = matched_by == "mapping_server_id_conflict"
        overlap_aircraft_ids = set() if identity_conflict else _overlap_aircraft_ids(
            conn, manifest, source_id or -1, content_identity_by_flight
        )
        overlap_conflict = len(overlap_aircraft_ids) > 1
        overlap_aircraft_id = next(iter(overlap_aircraft_ids), None)
        merge_plan = None
        if existing and overlap_aircraft_id is not None and int(existing["id"]) != overlap_aircraft_id:
            merge_plan = preflight_entity_merge(
                conn, "aircraft", int(existing["id"]), overlap_aircraft_id
            )
            if merge_plan.get("ok"):
                existing = _find_aircraft_by_id(conn, overlap_aircraft_id)
                matched_by = "overlapping_flight_merge"
            else:
                overlap_conflict = True
        if not existing and overlap_aircraft_id is not None and not overlap_conflict:
            existing = _find_aircraft_by_id(conn, overlap_aircraft_id)
            matched_by = "overlapping_flight" if existing else None
        if not existing and not overlap_conflict and server_model_id is not None:
            existing = _find_aircraft_by_name(conn, server_model_id, aircraft.get("name"))
            matched_by = "name" if existing else None
        action = "existing" if existing else "create"
        conflict_reason = None
        if identity_conflict:
            action = "conflict"
            conflict_reason = "mapping_server_id_mismatch"
        elif overlap_conflict:
            action = "conflict"
            conflict_reason = (
                (merge_plan.get("conflicts") or [{}])[0].get("reason")
                if merge_plan
                else "aircraft_overlap_multiple_targets"
            )
        if existing:
            known_version = _manifest_server_version(aircraft)
            current_version = _as_int(existing.get("version")) or 1
            target_name = _clean_text(aircraft.get("name")).strip()
            server_model_id = _as_int(server_model.get("id")) if server_model else None
            if identity_conflict or overlap_conflict:
                pass
            elif server_model_id is not None and _as_int(existing.get("model_id")) != server_model_id:
                action = "conflict"
                conflict_reason = "aircraft_model_mismatch"
            elif server_model_id is not None:
                name_owner = _find_aircraft_by_name(conn, server_model_id, target_name) if target_name else None
                merge_source_id = int(merge_plan["source_id"]) if merge_plan and merge_plan.get("ok") else None
                if (
                    name_owner
                    and int(name_owner["id"]) != int(existing["id"])
                    and int(name_owner["id"]) != merge_source_id
                ):
                    action = "conflict"
                    conflict_reason = "aircraft_name_conflict"
                elif merge_plan and merge_plan.get("ok"):
                    action = "merge"
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
            "server_newer": bool(
                existing and known_version is not None and current_version > known_version
            ),
            "matched_by": matched_by,
            "merge_plan": merge_plan if merge_plan and merge_plan.get("ok") else None,
            "reason": conflict_reason,
        }
        aircraft_plan.append(item)
        if source_id is not None and existing:
            aircraft_by_source[source_id] = existing

    planned_flight_redirects: dict[int, int] = {}
    planned_moved_flights: set[int] = set()
    for aircraft_item in aircraft_plan:
        plan = aircraft_item.get("merge_plan") or {}
        for pair in plan.get("duplicate_flights") or []:
            planned_flight_redirects[int(pair["source_flight_id"])] = int(pair["target_flight_id"])
        planned_moved_flights.update(int(value) for value in plan.get("move_flight_ids") or [])

    flight_plan: list[dict[str, Any]] = []
    for flight in manifest.get("flights") or []:
        source_id = _first_manifest_id(flight, "id", "source_id")
        source_aircraft_id = _first_manifest_id(flight, "aircraft_id", "source_aircraft_id")
        server_aircraft = aircraft_by_source.get(source_aircraft_id) if source_aircraft_id is not None else None
        existing, matched_by = _find_flight_identity(conn, flight, source_node_id)
        identity_conflict = matched_by == "mapping_server_id_conflict"
        package_identity = content_identity_by_flight.get(source_id or -1, ())
        content_matches = (
            [] if identity_conflict else _find_flights_by_content_identity(conn, package_identity)
        )
        content_match_ids = {int(row["id"]) for row in content_matches}
        content_conflict = len(content_match_ids) > 1
        planned_redirect_target = planned_flight_redirects.get(int(existing["id"])) if existing else None
        if planned_redirect_target is not None and planned_redirect_target in content_match_ids:
            existing = next(row for row in content_matches if int(row["id"]) == planned_redirect_target)
            matched_by = "planned_aircraft_merge"
        elif existing and content_match_ids and int(existing["id"]) not in content_match_ids:
            content_conflict = True
        if not existing and len(content_matches) == 1:
            existing = content_matches[0]
            matched_by = "raw_file_set"
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
        if identity_conflict:
            action = "conflict"
            reason = "mapping_server_id_mismatch"
        elif content_conflict:
            action = "conflict"
            reason = "flight_raw_set_ambiguous"
        if existing:
            known_version = _manifest_server_version(flight)
            current_version = _as_int(existing.get("version")) or 1
            server_identity = _server_flight_content_identity(conn, int(existing["id"]))
            server_aircraft_id = _as_int(server_aircraft.get("id")) if server_aircraft else None
            if identity_conflict or content_conflict:
                pass
            elif (
                server_aircraft_id is not None
                and _as_int(existing.get("aircraft_id")) != server_aircraft_id
                and int(existing["id"]) not in planned_moved_flights
            ):
                action = "conflict"
                reason = "flight_aircraft_mismatch"
            elif package_identity != server_identity:
                action = "conflict"
                reason = "flight_raw_hash_mismatch"
            elif existing.get("deleted_at") is not None:
                action = "restore"
                reason = "server_deleted"
            else:
                values = _flight_metadata_values(
                    flight,
                    _as_int(server_aircraft.get("id")) if server_aircraft else None,
                    source_node_id,
                )
                action = "update_metadata" if _flight_metadata_changed(existing, values) else "existing"

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
                "server_newer": bool(
                    existing and known_version is not None and current_version > known_version
                ) if existing else False,
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
        "missing_raw_objects": upload_sessions.missing_raw_objects(conn, manifest),
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
    source_config = _build_source_config(parsed, int(source_model["source_id"]))
    source_signature = model_structure_signature(source_config)
    source_identity = dict(source_model)
    source_identity["config_signature"] = source_signature
    existing = _find_model(conn, source_identity, source_node_id)
    if existing:
        if (
            source_signature
            and existing.get("config_signature")
            and source_signature != existing.get("config_signature")
        ):
            raise ValueError(
                f"Model structure conflict for client_uid={source_model.get('client_uid')}"
            )
        registry_changed = _ensure_model_registry(
            conn, parsed, int(source_model["source_id"]), int(existing["id"])
        )
        target_name = _clean_text(source_model.get("name")).strip()
        current_version = int(existing.get("version") or 1)
        if registry_changed or (target_name and target_name != existing.get("name")):
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
                    "name": target_name or existing.get("name"),
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
    created = db.create_model(conn, source_config)
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
) -> bool:
    config = _build_source_config(parsed, source_model_id)
    now = db.utcnow()
    metadata_changed = False
    for data_type_key, dt_def in config.get("data_types", {}).items():
        data_type_key = db.validate_data_type_key(data_type_key)
        table_name = db.server_data_table_name(server_model_id, data_type_key)
        db.create_dynamic_table(conn, server_model_id, data_type_key, dt_def.get("columns") or [])
        existing = conn.execute(
            db.text(
                """SELECT id, display_label FROM data_table_registry
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
            metadata_changed = True
        elif existing._mapping.get("display_label") != (dt_def.get("display_label") or data_type_key):
            conn.execute(
                db.text(
                    """UPDATE data_table_registry
                       SET display_label=:display_label, version=version+1, updated_at=:updated_at
                       WHERE id=:id"""
                ),
                {
                    "id": int(existing._mapping["id"]),
                    "display_label": dt_def.get("display_label") or data_type_key,
                    "updated_at": now,
                },
            )
            metadata_changed = True
        for index, raw_col in enumerate(dt_def.get("columns") or [], start=1):
            col = db.normalize_column(raw_col, index)
            existing_col = conn.execute(
                db.text(
                    """SELECT id, display_label, unit, scale_factor FROM column_registry
                       WHERE model_id=:model_id AND table_name=:table_name AND column_name=:column_name"""
                ),
                {
                    "model_id": server_model_id,
                    "table_name": table_name,
                    "column_name": col["name"],
                },
            ).first()
            if not existing_col:
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
                metadata_changed = True
                continue
            existing_values = existing_col._mapping
            if (
                existing_values.get("display_label") != col["display_label"]
                or _clean_text(existing_values.get("unit")) != _clean_text(col["unit"])
                or float(existing_values.get("scale_factor") or 1.0) != float(col["scale_factor"] or 1.0)
            ):
                conn.execute(
                    db.text(
                        """UPDATE column_registry
                           SET display_label=:display_label, unit=:unit, scale_factor=:scale_factor,
                               version=version+1, updated_at=:updated_at
                           WHERE id=:id"""
                    ),
                    {
                        "id": int(existing_values["id"]),
                        "display_label": col["display_label"],
                        "unit": col["unit"],
                        "scale_factor": col["scale_factor"],
                        "updated_at": now,
                    },
                )
                metadata_changed = True
    return metadata_changed


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
    planned_server_id: int | None = None,
) -> tuple[dict[str, Any], bool]:
    existing = (
        _find_aircraft_by_id(conn, planned_server_id)
        if planned_server_id is not None
        else _find_aircraft(conn, aircraft, server_model_id, source_node_id)
    )
    if existing:
        if _as_int(existing.get("model_id")) != int(server_model_id):
            raise ValueError(
                f"Aircraft parent conflict for client_uid={aircraft.get('client_uid')}"
            )
        target_name = _clean_text(aircraft.get("name")).strip()
        current_version = int(existing.get("version") or 1)
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
    planned_server_id: int | None = None,
) -> tuple[dict[str, Any], bool, bool]:
    existing = None
    if planned_server_id is not None:
        row = conn.execute(
            db.text("SELECT * FROM flights WHERE id=:flight_id"),
            {"flight_id": int(planned_server_id)},
        ).first()
        existing = _row_dict(row)
    if not existing:
        existing, _ = _find_flight_identity(conn, flight, source_node_id)
    if not existing:
        existing = _find_flight_by_business(
            conn,
            server_aircraft_id,
            flight.get("flight_date"),
            flight.get("session_key"),
        )
    if existing:
        if _as_int(existing.get("aircraft_id")) != int(server_aircraft_id):
            raise ValueError(
                f"Flight parent conflict for client_uid={flight.get('client_uid')}"
            )
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
        current_version = int(existing.get("version") or 1)
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
        if not row:
            return candidate
        index += 1


def _attach_raw_file(
    conn,
    raw: dict[str, Any],
    server_flight_id: int,
    bundle_path: str | None,
) -> tuple[int | None, bool, str | None]:
    expected_sha = str(raw.get("sha256") or "").lower()
    expected_size = _as_int(raw.get("size_bytes"))
    if not re.match(r"^[0-9a-fA-F]{64}$", expected_sha) or expected_size is None:
        return None, False, "raw file has invalid sha256 or size"

    package_path = raw.get("package_path") or f"raw_files/{raw.get('storage_rel_path') or ''}"
    package_path = _safe_zip_path(package_path)
    existing_identity_rows = conn.execute(
        db.text(
            """SELECT id FROM flight_raw_files
               WHERE flight_id=:flight_id AND sha256=:sha256
                 AND original_rel_path=:original_rel_path
                 AND ((data_type_key IS NULL AND :data_type_key IS NULL)
                      OR data_type_key=:data_type_key)"""
        ),
        {
            "flight_id": server_flight_id,
            "sha256": expected_sha,
            "original_rel_path": _clean_text(raw.get("original_rel_path")),
            "data_type_key": raw.get("data_type_key"),
        },
    ).fetchall()
    link_ordinal = max(0, int(raw.get("_link_ordinal") or 0))
    if link_ordinal < len(existing_identity_rows):
        return int(existing_identity_rows[link_ordinal]._mapping["id"]), False, None
    desired_rel = _server_storage_rel_path(conn, server_flight_id, raw)
    storage_rel_path = _unique_server_storage_rel_path(conn, desired_rel, server_flight_id)
    try:
        if bundle_path:
            upload_sessions.ensure_raw_object_from_zip(
                conn,
                bundle_path,
                package_path,
                expected_sha.lower(),
                expected_size,
            )
        elif not upload_sessions.raw_object_exists(conn, expected_sha.lower(), expected_size):
            raise ValueError(f"raw object is missing: {expected_sha}")
    except Exception as exc:
        return None, False, str(exc)
    existing = conn.execute(
        db.text(
            """SELECT id, sha256, size_bytes FROM flight_raw_files
               WHERE flight_id=:flight_id
                  AND storage_rel_path=:storage_rel_path"""
        ),
        {
            "flight_id": server_flight_id,
            "storage_rel_path": storage_rel_path,
        },
    ).first()
    if existing:
        if (
            str(existing._mapping["sha256"]) != expected_sha
            or int(existing._mapping["size_bytes"]) != expected_size
        ):
            return None, False, "raw link path already refers to different content"
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
            "storage_rel_path": storage_rel_path,
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
    *,
    metrics: SyncMetrics | None = None,
    operation_id: str | None = None,
    table_totals: dict[str, int] | None = None,
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
        with (
            metrics.phase("server_dynamic_delete", table_name=server_table)
            if metrics
            else nullcontext({})
        ) as delete_metric:
            deleted = 0
            for server_flight_id in sorted(set(flight_map.values())):
                result = conn.execute(
                    db.text(f"DELETE FROM {server_table_q} WHERE flight_id=:flight_id"),
                    {"flight_id": server_flight_id},
                )
                deleted += max(0, int(result.rowcount or 0))
            delete_metric["rows"] = deleted
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
        insert_batch_size = _dynamic_insert_batch_size(len(columns))
        table_inserted = 0
        with (
            metrics.phase("server_dynamic_insert", table_name=server_table)
            if metrics
            else nullcontext({})
        ) as insert_metric:
            while rows := cursor.fetchmany(insert_batch_size):
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
                    table_inserted += len(batch)
                    server_operations.update(
                        operation_id,
                        phase="server_dynamic_insert",
                        message=f"Writing parsed table {server_table}",
                        current=table_inserted,
                        total=(table_totals or {}).get(source_table),
                        unit="rows",
                        table_name=server_table,
                    )
            insert_metric["rows"] = table_inserted
    return inserted


def import_push_bundle(
    conn,
    bundle_path: str,
    imported_by: int | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    bundle_abs = os.path.abspath(bundle_path)
    metrics = SyncMetrics("server_push_import", operation_id)
    tmp_dir = tempfile.mkdtemp(prefix="flightanalyzer_server_push_")
    try:
        server_operations.update(
            operation_id,
            phase="server_extract_validate",
            message="Extracting and validating upload bundle",
        )
        with metrics.phase("server_extract_validate") as phase_metric:
            manifest, parsed_path = _extract_bundle(bundle_abs, tmp_dir)
            phase_metric.update(
                bundle_bytes=os.path.getsize(bundle_abs),
                parsed_bytes=os.path.getsize(parsed_path),
            )
            metrics.sample_temp(tmp_dir, bundle_abs)
        package_id = str(manifest["package_id"])
        source_node_id = str(manifest["source_node_id"])
        duplicate = existing_import_report(conn, package_id, source_node_id)
        if duplicate:
            return duplicate

        server_operations.update(
            operation_id,
            phase="server_preflight",
            message="Matching models, aircraft, and flights",
            current=0,
            total=sum(len(manifest.get(key) or []) for key in ("models", "aircraft", "flights")),
            unit="entities",
        )
        with metrics.phase("server_preflight") as phase_metric:
            preflight = build_preflight_plan(conn, manifest)
            phase_metric["entities"] = sum(
                len(manifest.get(key) or []) for key in ("models", "aircraft", "flights")
            )
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
            report["metrics"] = metrics.result("failed")
            _record_import(conn, package_id, source_node_id, imported_by, "conflict", report)
            return report

        parsed = sqlite3.connect(parsed_path)
        parsed.row_factory = sqlite3.Row
        try:
            report = _import_parsed_bundle(
                conn,
                parsed,
                bundle_abs,
                manifest,
                imported_by,
                preflight,
                metrics=metrics,
                operation_id=operation_id,
            )
        finally:
            parsed.close()
        _record_import(conn, package_id, source_node_id, imported_by, report["status"], report)
        server_operations.update(
            operation_id,
            phase="server_archive",
            message="Archiving imported sync bundle",
        )
        with metrics.phase("server_archive") as phase_metric:
            _copy_bundle_archive(bundle_abs, package_id, source_node_id)
            phase_metric["bytes"] = os.path.getsize(bundle_abs)
        report["metrics"] = metrics.result()
        return report
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def import_push_session(
    conn,
    session_id: str,
    *,
    imported_by: int | None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    manifest, parsed_path, session = upload_sessions.session_import_inputs(conn, session_id)
    if session.get("status") == "completed":
        result = session.get("result_json")
        if isinstance(result, str):
            return json.loads(result)
        return result or {"ok": True, "status": "success"}
    manifest = validate_manifest(manifest)
    package_id = str(manifest["package_id"])
    source_node_id = str(manifest["source_node_id"])
    duplicate = existing_import_report(conn, package_id, source_node_id)
    if duplicate:
        upload_sessions.mark_completed(conn, session_id, duplicate)
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
        upload_sessions.mark_completed(conn, session_id, report)
        return report

    upload_sessions.mark_importing(conn, session_id)
    metrics = SyncMetrics("server_session_import", operation_id)
    parsed = sqlite3.connect(parsed_path)
    parsed.row_factory = sqlite3.Row
    try:
        with metrics.phase("server_session_database_import"):
            report = _import_parsed_bundle(
                conn,
                parsed,
                None,
                manifest,
                imported_by,
                preflight,
                metrics=metrics,
                operation_id=operation_id,
            )
    finally:
        parsed.close()
    report["metrics"] = metrics.result()
    _record_import(conn, package_id, source_node_id, imported_by, report["status"], report)
    upload_sessions.mark_completed(conn, session_id, report)
    return report


def _import_parsed_bundle(
    conn,
    parsed: sqlite3.Connection,
    bundle_path: str | None,
    manifest: dict[str, Any],
    imported_by: int | None,
    preflight: dict[str, Any],
    *,
    metrics: SyncMetrics | None = None,
    operation_id: str | None = None,
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
    model_preflight = {
        int(item["source_id"]): item
        for item in preflight.get("models") or []
        if item.get("source_id") is not None
    }
    aircraft_preflight = {
        int(item["source_id"]): item
        for item in preflight.get("aircraft") or []
        if item.get("source_id") is not None
    }
    flight_preflight = {
        int(item["source_id"]): item
        for item in preflight.get("flights") or []
        if item.get("source_id") is not None
    }
    merge_results = []
    entity_started = time.perf_counter()
    entity_total = len(model_rows) + len(aircraft_rows) + len(flight_rows)
    entity_current = 0
    for item in aircraft_preflight.values():
        merge_plan = item.get("merge_plan") or {}
        if not merge_plan:
            continue
        merge_result = execute_entity_merge(
            conn,
            "aircraft",
            int(merge_plan["source_id"]),
            int(merge_plan["target_id"]),
            created_by=imported_by,
        )
        if not merge_result.get("ok"):
            raise ValueError(f"Aircraft merge changed after preflight: {merge_result.get('conflicts')}")
        merge_results.append(merge_result)

    for source_model in model_rows:
        source_id = int(source_model["source_id"])
        server_model, created = _ensure_model(conn, parsed, source_model, package_id, source_node_id)
        _record_entity_mapping(
            conn,
            source_node_id,
            "model",
            source_model.get("client_uid"),
            int(server_model["id"]),
            "create" if created else (model_preflight.get(source_id) or {}).get("matched_by") or "existing",
        )
        model_map[source_id] = server_model
        if created:
            imported_counts["models"] += 1
        else:
            existing_counts["models"] += 1
        mappings["models"].append(
            _mapping_item(source_id, server_model, source_model.get("client_uid"))
        )
        entity_current += 1
        server_operations.update(
            operation_id,
            phase="server_entities",
            message="Creating or updating server entities",
            current=entity_current,
            total=entity_total,
            unit="entities",
        )

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
            _as_int((aircraft_preflight.get(source_id) or {}).get("server_id")),
        )
        _record_entity_mapping(
            conn,
            source_node_id,
            "aircraft",
            source_aircraft.get("client_uid"),
            int(server_aircraft["id"]),
            "create" if created else (aircraft_preflight.get(source_id) or {}).get("matched_by") or "existing",
        )
        aircraft_map[source_id] = server_aircraft
        if created:
            imported_counts["aircraft"] += 1
        else:
            existing_counts["aircraft"] += 1
        mappings["aircraft"].append(
            _mapping_item(source_id, server_aircraft, source_aircraft.get("client_uid"))
        )
        entity_current += 1
        server_operations.update(
            operation_id,
            phase="server_entities",
            message="Creating or updating server entities",
            current=entity_current,
            total=entity_total,
            unit="entities",
        )

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
            _as_int((flight_preflight.get(source_id) or {}).get("server_id")),
        )
        _record_entity_mapping(
            conn,
            source_node_id,
            "flight",
            source_flight.get("client_uid"),
            int(server_flight["id"]),
            "create" if created else (flight_preflight.get(source_id) or {}).get("matched_by") or "existing",
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
        mappings["flights"].append(
            _mapping_item(source_id, server_flight, source_flight.get("client_uid"))
        )
        entity_current += 1
        server_operations.update(
            operation_id,
            phase="server_entities",
            message="Creating or updating server entities",
            current=entity_current,
            total=entity_total,
            unit="entities",
        )

    if metrics:
        metrics.record_phase(
            "server_entities",
            time.perf_counter() - entity_started,
            entities=entity_current,
        )

    for source_model_id, server_model in model_map.items():
        rows = _insert_dynamic_rows(
            conn,
            parsed,
            source_model_id,
            int(server_model["id"]),
            {source_id: int(row["id"]) for source_id, row in flight_map.items() if source_id in refreshed_source_flights},
            metrics=metrics,
            operation_id=operation_id,
            table_totals=(manifest.get("parsed_data") or {}).get("table_rows") or {},
        )
        imported_counts["dynamic_rows"] += rows

    raw_started = time.perf_counter()
    raw_rows = manifest.get("raw_files") or []
    raw_link_ordinals: dict[tuple[int, str, str, str], int] = defaultdict(int)
    for raw_index, raw in enumerate(raw_rows, start=1):
        server_operations.update(
            operation_id,
            phase="server_raw_store",
            message="Validating and storing raw files",
            current=raw_index,
            total=len(raw_rows),
            unit="files",
            file_name=raw.get("original_rel_path") or raw.get("original_name"),
        )
        source_flight_id = _first_manifest_id(raw, "flight_id", "source_flight_id")
        if source_flight_id is None or source_flight_id not in flight_map:
            continue
        raw_identity = (
            int(source_flight_id),
            str(raw.get("sha256") or "").lower(),
            _clean_text(raw.get("data_type_key")),
            _clean_text(raw.get("original_rel_path")),
        )
        raw_with_ordinal = {**raw, "_link_ordinal": raw_link_ordinals[raw_identity]}
        raw_link_ordinals[raw_identity] += 1
        raw_link_id, created, warning = _attach_raw_file(
            conn,
            raw_with_ordinal,
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
    if metrics:
        metrics.record_phase(
            "server_raw_store",
            time.perf_counter() - raw_started,
            files=len(raw_rows),
            bytes=sum(int(row.get("size_bytes") or 0) for row in raw_rows),
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
        "merges": merge_results,
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
    cleanup_files(bundles_dir, max_age_seconds=7 * 24 * 60 * 60)
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
        rows = []
        for batch in _batched_ids(ids["aircraft"]):
            placeholders = ", ".join(f":id{i}" for i, _ in enumerate(batch))
            rows.extend(conn.execute(
                db.text(f"SELECT id FROM flights WHERE aircraft_id IN ({placeholders})"),
                {f"id{i}": value for i, value in enumerate(batch)},
            ).fetchall())
        ids["flights"].update(int(row._mapping["id"]) for row in rows)
    if ids["models"]:
        rows = []
        for batch in _batched_ids(ids["models"]):
            placeholders = ", ".join(f":id{i}" for i, _ in enumerate(batch))
            rows.extend(conn.execute(
                db.text(f"SELECT id FROM aircraft WHERE model_id IN ({placeholders})"),
                {f"id{i}": value for i, value in enumerate(batch)},
            ).fetchall())
        aircraft_ids = {int(row._mapping["id"]) for row in rows}
        ids["aircraft"].update(aircraft_ids)
        if aircraft_ids:
            rows = []
            for batch in _batched_ids(aircraft_ids):
                placeholders = ", ".join(f":id{i}" for i, _ in enumerate(batch))
                rows.extend(conn.execute(
                    db.text(f"SELECT id FROM flights WHERE aircraft_id IN ({placeholders})"),
                    {f"id{i}": value for i, value in enumerate(batch)},
                ).fetchall())
            ids["flights"].update(int(row._mapping["id"]) for row in rows)

    if ids["flights"]:
        rows = []
        for batch in _batched_ids(ids["flights"]):
            placeholders = ", ".join(f":id{i}" for i, _ in enumerate(batch))
            rows.extend(conn.execute(
                db.text(
                    f"""SELECT f.aircraft_id, a.model_id
                        FROM flights f
                        JOIN aircraft a ON a.id=f.aircraft_id
                        WHERE f.id IN ({placeholders})"""
                ),
                {f"id{i}": value for i, value in enumerate(batch)},
            ).fetchall())
        ids["aircraft"].update(int(row._mapping["aircraft_id"]) for row in rows)
        ids["models"].update(int(row._mapping["model_id"]) for row in rows)
    if ids["aircraft"]:
        rows = []
        for batch in _batched_ids(ids["aircraft"]):
            placeholders = ", ".join(f":id{i}" for i, _ in enumerate(batch))
            rows.extend(conn.execute(
                db.text(f"SELECT model_id FROM aircraft WHERE id IN ({placeholders})"),
                {f"id{i}": value for i, value in enumerate(batch)},
            ).fetchall())
        ids["models"].update(int(row._mapping["model_id"]) for row in rows)
    return ids


def _select_rows_by_ids(conn, table: str, ids: set[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = []
    for batch in _batched_ids(ids):
        placeholders = ", ".join(f":id{i}" for i, _ in enumerate(batch))
        params = {f"id{i}": value for i, value in enumerate(batch)}
        rows.extend(conn.execute(
            db.text(f"SELECT * FROM {table} WHERE id IN ({placeholders}) ORDER BY id"),
            params,
        ).fetchall())
    return sorted(
        (_row_dict(row) or {} for row in rows),
        key=lambda row: int(row["id"]),
    )


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


def _attach_server_model_configs(conn, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        row["config"] = _server_model_config(conn, int(row["id"]))
    return rows


def _write_server_parsed_sqlite(conn, ids: dict[str, set[int]], out_path: str) -> dict[str, Any]:
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
        table_rows: dict[str, int] = {}
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
            if not ids["flights"]:
                continue
            insert_cols = ["source_id"] + [
                ("source_flight_id" if col == "flight_id" else col)
                for col in source_cols
                if col != "id"
            ]
            insert_sql = (
                f"INSERT INTO {source_table_q} "
                f"({', '.join(_q_sqlite(c) for c in insert_cols)}) "
                f"VALUES ({','.join('?' for _ in insert_cols)})"
            )
            table_count = 0
            for flight_batch in _batched_ids(ids["flights"]):
                placeholders = ", ".join(f":fid{i}" for i, _ in enumerate(flight_batch))
                params = {f"fid{i}": fid for i, fid in enumerate(flight_batch)}
                result = conn.execute(
                    db.text(
                        f"SELECT * FROM {server_table_q} WHERE flight_id IN ({placeholders}) ORDER BY flight_id, id"
                    ),
                    params,
                )
                while rows := result.fetchmany(_dynamic_insert_batch_size(len(source_cols))):
                    sqlite_conn.executemany(
                        insert_sql,
                        [
                            [_sqlite_value(row._mapping.get("id"))]
                            + [
                                _sqlite_value(row._mapping.get(col))
                                for col in source_cols
                                if col != "id"
                            ]
                            for row in rows
                        ],
                    )
                    parsed_rows += len(rows)
                    table_count += len(rows)
            table_rows[server_table] = table_count
        sqlite_conn.commit()
        return {"rows": parsed_rows, "table_rows": table_rows}
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
    model_rows = _attach_server_model_configs(
        conn, _select_rows_by_ids(conn, "aircraft_models", ids["models"])
    )
    aircraft_rows = _select_rows_by_ids(conn, "aircraft", ids["aircraft"])
    flight_rows = _select_rows_by_ids(conn, "flights", ids["flights"])
    raw_rows = _server_raw_manifest_rows(conn, ids["flights"])
    redirects = _entity_redirects_since(conn, since)
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
        "entity_redirects": redirects,
        "summary": {
            "models": len(model_rows),
            "aircraft": len(aircraft_rows),
            "flights": len(flight_rows),
            "raw_files": len(raw_rows),
            "entity_redirects": len(redirects),
        },
    }


def build_pull_bundle(
    conn,
    since: int | str | None = None,
    *,
    exclude_source_node_id: str | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Create a pull_bundle zip from server state and return its path."""
    metrics = SyncMetrics("server_pull_bundle", operation_id)
    server_operations.update(
        operation_id,
        phase="server_pull_query",
        message="Selecting changed server entities",
    )
    with metrics.phase("server_pull_query") as phase_metric:
        ids = _changed_entity_ids(conn, since, exclude_source_node_id=exclude_source_node_id)
        current_cursor = _max_cursor(conn)
        phase_metric.update(**{key: len(value) for key, value in ids.items()})
    package_id = f"pkg-{uuid.uuid4().hex}"
    source_node_id = "server"
    tmp_dir = tempfile.mkdtemp(prefix="flightanalyzer_server_pull_")
    try:
        parsed_path = os.path.join(tmp_dir, "parsed.sqlite")
        server_operations.update(
            operation_id,
            phase="server_pull_parsed_export",
            message="Writing pull parsed data",
        )
        with metrics.phase("server_pull_parsed_export") as phase_metric:
            parsed_result = _write_server_parsed_sqlite(conn, ids, parsed_path)
            if isinstance(parsed_result, dict):
                parsed_rows = int(parsed_result.get("rows") or 0)
                parsed_table_rows = parsed_result.get("table_rows") or {}
            else:
                parsed_rows = int(parsed_result or 0)
                parsed_table_rows = {}
            parsed_size = os.path.getsize(parsed_path)
            phase_metric.update(rows=parsed_rows, bytes=parsed_size)
        with metrics.phase("server_pull_hash") as phase_metric:
            parsed_sha = _sha256_file(parsed_path)
            phase_metric["bytes"] = parsed_size

        with metrics.phase("server_pull_manifest") as phase_metric:
            model_rows = _attach_server_model_configs(
                conn, _select_rows_by_ids(conn, "aircraft_models", ids["models"])
            )
            aircraft_rows = _select_rows_by_ids(conn, "aircraft", ids["aircraft"])
            flight_rows = _select_rows_by_ids(conn, "flights", ids["flights"])
            raw_rows = _server_raw_manifest_rows(conn, ids["flights"])
            redirects = _entity_redirects_since(conn, since)
            phase_metric.update(
                models=len(model_rows),
                aircraft=len(aircraft_rows),
                flights=len(flight_rows),
                raw_files=len(raw_rows),
                redirects=len(redirects),
            )
        manifest = {
            "package_version": 2,
            "sync_protocol_version": 1,
            "package_id": package_id,
            "bundle_kind": "pull_bundle",
            "app_version": "2.0.0",
            "schema_version": CURRENT_SCHEMA_VERSION,
            "source_node_id": source_node_id,
            "source_environment": "server",
            "exported_at": db.utcnow().isoformat(timespec="seconds"),
            "base_server_cursor": str(since or ""),
            "server_cursor": current_cursor,
            "models": model_rows,
            "aircraft": aircraft_rows,
            "flights": flight_rows,
            "raw_files": raw_rows,
            "entity_redirects": redirects,
            "parsed_data": {
                "format": "sqlite",
                "path": "data/parsed.sqlite",
                "sha256": parsed_sha,
                "size_bytes": parsed_size,
                "table_rows": parsed_table_rows,
            },
        }

        bundles_dir = os.path.join(db.SERVER_DATA_DIR, "bundles")
        os.makedirs(bundles_dir, exist_ok=True)
        cleanup_files(bundles_dir, max_age_seconds=7 * 24 * 60 * 60)
        bundle_path = os.path.join(bundles_dir, f"server_pull_{current_cursor}_{package_id}.fapkg")
        manifest_path = os.path.join(tmp_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, default=_json_default)
        server_operations.update(
            operation_id,
            phase="server_pull_zip",
            message="Compressing pull bundle",
            current=0,
            total=len(raw_rows),
            unit="files",
        )
        with metrics.phase("server_pull_zip") as phase_metric:
            with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(manifest_path, "manifest.json")
                zf.write(parsed_path, "data/parsed.sqlite")
                for model_id in sorted(ids["models"]):
                    zf.writestr(
                        f"models/model_{model_id}.json",
                        json.dumps(_server_model_config(conn, model_id), ensure_ascii=False, indent=2, default=_json_default),
                    )
                written_raw_objects: set[str] = set()
                for raw_index, raw in enumerate(raw_rows, start=1):
                    sha = str(raw["sha256"]).lower()
                    if sha in written_raw_objects:
                        continue
                    src = upload_sessions.raw_object_abs_path(sha)
                    root = os.path.abspath(os.path.join(db.SERVER_DATA_DIR, "raw_files"))
                    if os.path.commonpath([src, root]) != root or not os.path.exists(src):
                        continue
                    zf.write(src, _safe_zip_path(raw["package_path"]))
                    written_raw_objects.add(sha)
                    server_operations.update(
                        operation_id,
                        phase="server_pull_zip",
                        message="Compressing pull bundle",
                        current=raw_index,
                        total=len(raw_rows),
                        unit="files",
                        file_name=raw.get("original_rel_path") or raw.get("original_name"),
                    )
            phase_metric.update(
                files=len(raw_rows) + len(ids["models"]) + 2,
                output_bytes=os.path.getsize(bundle_path),
            )
        metrics.sample_temp(tmp_dir, bundle_path)
        result = {
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
                "entity_redirects": len(redirects),
                "parsed_rows": parsed_rows,
            },
        }
        result["metrics"] = metrics.result()
        return result
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _server_raw_manifest_rows(conn, flight_ids: set[int]) -> list[dict[str, Any]]:
    if not flight_ids:
        return []
    rows = []
    for batch in _batched_ids(flight_ids):
        placeholders = ", ".join(f":fid{i}" for i, _ in enumerate(batch))
        params = {f"fid{i}": fid for i, fid in enumerate(batch)}
        rows.extend(conn.execute(
            db.text(
                f"""SELECT frf.id, frf.flight_id, frf.original_name, frf.original_rel_path,
                          frf.storage_rel_path, frf.sha256, frf.size_bytes,
                          frf.data_type_key, frf.source_mtime, frf.created_at
                   FROM flight_raw_files frf
                   WHERE frf.flight_id IN ({placeholders})
                   ORDER BY frf.flight_id, frf.original_rel_path, frf.id"""
            ),
            params,
        ).fetchall())
    rows.sort(
        key=lambda row: (
            int(row._mapping["flight_id"]),
            str(row._mapping["original_rel_path"]),
            int(row._mapping["id"]),
        )
    )
    raw_files = []
    for row in rows:
        item = _row_dict(row) or {}
        storage_rel_path = _safe_zip_path(item["storage_rel_path"])
        item["storage_rel_path"] = storage_rel_path
        item["package_path"] = _safe_zip_path(f"raw_objects/{item['sha256']}")
        raw_files.append(item)
    return raw_files


def _active_aircraft_flights(conn, aircraft_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        db.text(
            """SELECT * FROM flights
               WHERE aircraft_id=:aircraft_id AND deleted_at IS NULL
               ORDER BY id"""
        ),
        {"aircraft_id": int(aircraft_id)},
    ).fetchall()
    return [_row_dict(row) or {} for row in rows]


def _aircraft_merge_plan(
    conn,
    source_aircraft_id: int,
    target_aircraft_id: int,
    *,
    allow_different_models: bool,
) -> dict[str, Any]:
    source = _find_aircraft_by_id(conn, source_aircraft_id)
    target = _find_aircraft_by_id(conn, target_aircraft_id)
    conflicts: list[dict[str, Any]] = []
    if not source or source.get("deleted_at") is not None:
        conflicts.append({"reason": "merge_source_not_active", "source_id": source_aircraft_id})
    if not target or target.get("deleted_at") is not None:
        conflicts.append({"reason": "merge_target_not_active", "target_id": target_aircraft_id})
    if source_aircraft_id == target_aircraft_id:
        conflicts.append({"reason": "merge_source_equals_target"})
    if source and target and not allow_different_models and int(source["model_id"]) != int(target["model_id"]):
        conflicts.append({"reason": "aircraft_model_mismatch"})
    duplicate_flights: list[dict[str, int]] = []
    move_flights: list[int] = []
    if source and target and not conflicts:
        for source_flight in _active_aircraft_flights(conn, source_aircraft_id):
            target_flight = _find_flight_by_business(
                conn,
                target_aircraft_id,
                source_flight.get("flight_date"),
                source_flight.get("session_key"),
            )
            if target_flight and target_flight.get("deleted_at") is not None:
                conflicts.append(
                    {
                        "reason": "target_deleted_business_key_collision",
                        "source_flight_id": int(source_flight["id"]),
                        "target_flight_id": int(target_flight["id"]),
                    }
                )
                continue
            if not target_flight:
                move_flights.append(int(source_flight["id"]))
                continue
            source_identity = _server_flight_content_identity(conn, int(source_flight["id"]))
            target_identity = _server_flight_content_identity(conn, int(target_flight["id"]))
            if source_identity != target_identity:
                conflicts.append(
                    {
                        "reason": "flight_business_key_raw_mismatch",
                        "source_flight_id": int(source_flight["id"]),
                        "target_flight_id": int(target_flight["id"]),
                        "flight_date": source_flight.get("flight_date"),
                        "session_key": source_flight.get("session_key"),
                    }
                )
                continue
            duplicate_flights.append(
                {
                    "source_flight_id": int(source_flight["id"]),
                    "target_flight_id": int(target_flight["id"]),
                }
            )
    return {
        "entity_type": "aircraft",
        "source_id": int(source_aircraft_id),
        "target_id": int(target_aircraft_id),
        "source": source,
        "target": target,
        "move_flight_ids": move_flights,
        "duplicate_flights": duplicate_flights,
        "conflicts": conflicts,
        "ok": not conflicts,
    }


def _target_aircraft_candidates_for_model_merge(conn, source_aircraft_id: int, target_model_id: int) -> set[int]:
    candidates: set[int] = set()
    for flight in _active_aircraft_flights(conn, source_aircraft_id):
        content_identity = _server_flight_content_identity(conn, int(flight["id"]))
        for match in _find_flights_by_content_identity(conn, content_identity):
            aircraft = _find_aircraft_by_id(conn, int(match["aircraft_id"]))
            if aircraft and int(aircraft["model_id"]) == int(target_model_id):
                candidates.add(int(aircraft["id"]))
    return candidates


def preflight_entity_merge(conn, entity_type: str, source_id: int, target_id: int) -> dict[str, Any]:
    source_id = int(source_id)
    target_id = _entity_redirect_target(conn, entity_type, int(target_id))
    if entity_type == "aircraft":
        plan = _aircraft_merge_plan(conn, source_id, target_id, allow_different_models=False)
        return {**plan, "status": "ready" if plan["ok"] else "conflict"}
    if entity_type != "model":
        raise ValueError(f"Unsupported merge entity type: {entity_type}")

    source = _row_dict(conn.execute(db.text("SELECT * FROM aircraft_models WHERE id=:id"), {"id": source_id}).first())
    target = _row_dict(conn.execute(db.text("SELECT * FROM aircraft_models WHERE id=:id"), {"id": target_id}).first())
    conflicts: list[dict[str, Any]] = []
    if not source or source.get("deleted_at") is not None:
        conflicts.append({"reason": "merge_source_not_active", "source_id": source_id})
    if not target or target.get("deleted_at") is not None:
        conflicts.append({"reason": "merge_target_not_active", "target_id": target_id})
    if source_id == target_id:
        conflicts.append({"reason": "merge_source_equals_target"})
    if source and target and source.get("config_signature") != target.get("config_signature"):
        conflicts.append({"reason": "model_config_mismatch"})

    aircraft_merges: list[dict[str, Any]] = []
    move_aircraft_ids: list[int] = []
    if source and target and not conflicts:
        source_aircraft_rows = conn.execute(
            db.text("SELECT * FROM aircraft WHERE model_id=:model_id AND deleted_at IS NULL ORDER BY id"),
            {"model_id": source_id},
        ).fetchall()
        for source_aircraft_row in source_aircraft_rows:
            source_aircraft = _row_dict(source_aircraft_row) or {}
            source_aircraft_id = int(source_aircraft["id"])
            candidates = _target_aircraft_candidates_for_model_merge(conn, source_aircraft_id, target_id)
            if len(candidates) > 1:
                conflicts.append(
                    {
                        "reason": "aircraft_overlap_multiple_targets",
                        "source_aircraft_id": source_aircraft_id,
                        "target_aircraft_ids": sorted(candidates),
                    }
                )
                continue
            if len(candidates) == 1:
                target_aircraft_id = next(iter(candidates))
                aircraft_plan = _aircraft_merge_plan(
                    conn, source_aircraft_id, target_aircraft_id, allow_different_models=True
                )
                aircraft_merges.append(aircraft_plan)
                conflicts.extend(aircraft_plan["conflicts"])
                continue
            name_owner = _find_aircraft_by_name(conn, target_id, _clean_text(source_aircraft.get("name")))
            if name_owner:
                conflicts.append(
                    {
                        "reason": "aircraft_name_collision_without_overlap",
                        "source_aircraft_id": source_aircraft_id,
                        "target_aircraft_id": int(name_owner["id"]),
                    }
                )
                continue
            move_aircraft_ids.append(source_aircraft_id)

    return {
        "ok": not conflicts,
        "status": "ready" if not conflicts else "conflict",
        "entity_type": "model",
        "source_id": source_id,
        "target_id": target_id,
        "source": source,
        "target": target,
        "move_aircraft_ids": move_aircraft_ids,
        "aircraft_merges": aircraft_merges,
        "conflicts": conflicts,
    }


def _server_dynamic_tables_by_type(conn, model_id: int) -> dict[str, str]:
    rows = conn.execute(
        db.text("SELECT data_type_key, table_name FROM data_table_registry WHERE model_id=:model_id"),
        {"model_id": int(model_id)},
    ).fetchall()
    return {str(row._mapping["data_type_key"]): str(row._mapping["table_name"]) for row in rows}


def _delete_server_dynamic_rows(conn, model_id: int, flight_id: int) -> None:
    for table_name in _server_dynamic_tables_by_type(conn, model_id).values():
        conn.execute(
            db.text(f"DELETE FROM {db.quote_identifier(table_name)} WHERE flight_id=:flight_id"),
            {"flight_id": int(flight_id)},
        )


def _transfer_server_dynamic_rows(conn, source_model_id: int, target_model_id: int, flight_id: int) -> None:
    if int(source_model_id) == int(target_model_id):
        return
    source_tables = _server_dynamic_tables_by_type(conn, source_model_id)
    target_tables = _server_dynamic_tables_by_type(conn, target_model_id)
    for data_type_key, source_table in source_tables.items():
        target_table = target_tables.get(data_type_key)
        if not target_table:
            raise ValueError(f"Target model is missing data type: {data_type_key}")
        source_columns = {
            str(row._mapping["Field"])
            for row in conn.execute(db.text(f"SHOW COLUMNS FROM {db.quote_identifier(source_table)}")).fetchall()
        }
        target_columns = {
            str(row._mapping["Field"])
            for row in conn.execute(db.text(f"SHOW COLUMNS FROM {db.quote_identifier(target_table)}")).fetchall()
        }
        columns = sorted((source_columns & target_columns) - {"id"})
        if "flight_id" not in columns:
            raise ValueError(f"Dynamic table has no flight_id column: {source_table}")
        quoted_columns = ", ".join(db.quote_identifier(column) for column in columns)
        conn.execute(
            db.text(
                f"""INSERT INTO {db.quote_identifier(target_table)} ({quoted_columns})
                    SELECT {quoted_columns} FROM {db.quote_identifier(source_table)}
                    WHERE flight_id=:flight_id"""
            ),
            {"flight_id": int(flight_id)},
        )
        conn.execute(
            db.text(f"DELETE FROM {db.quote_identifier(source_table)} WHERE flight_id=:flight_id"),
            {"flight_id": int(flight_id)},
        )


def _touch_merged_target(conn, entity_type: str, entity_id: int) -> int:
    table = ENTITY_TABLES[entity_type]
    row = conn.execute(db.text(f"SELECT version FROM {table} WHERE id=:id"), {"id": int(entity_id)}).first()
    if not row:
        raise KeyError(entity_type)
    version = int(row._mapping.get("version") or 1) + 1
    now = db.utcnow()
    conn.execute(
        db.text(f"UPDATE {table} SET version=:version, updated_at=:updated_at WHERE id=:id"),
        {"version": version, "updated_at": now, "id": int(entity_id)},
    )
    _insert_change_with_type(
        conn,
        "aircraft_model" if entity_type == "model" else entity_type,
        int(entity_id),
        "merge_update",
        version,
        "",
        "",
    )
    return version


def _mark_server_entity_merged(
    conn,
    entity_type: str,
    source_id: int,
    target_id: int,
    *,
    created_by: int | None,
) -> None:
    table = ENTITY_TABLES[entity_type]
    row = conn.execute(db.text(f"SELECT version FROM {table} WHERE id=:id"), {"id": int(source_id)}).first()
    if not row:
        raise KeyError(entity_type)
    version = int(row._mapping.get("version") or 1) + 1
    now = db.utcnow()
    if entity_type == "flight":
        conn.execute(
            db.text(
                """UPDATE flights
                   SET deleted_at=:deleted_at, deleted_by=:deleted_by,
                       delete_reason=:reason, version=:version, updated_at=:updated_at
                   WHERE id=:id"""
            ),
            {
                "deleted_at": now,
                "deleted_by": created_by,
                "reason": f"merged_into:{target_id}",
                "version": version,
                "updated_at": now,
                "id": int(source_id),
            },
        )
    else:
        conn.execute(
            db.text(
                f"""UPDATE {table}
                    SET name=CONCAT(LEFT(name, 220), '__merged_', id),
                        deleted_at=:deleted_at, version=:version, updated_at=:updated_at
                    WHERE id=:id"""
            ),
            {"deleted_at": now, "version": version, "updated_at": now, "id": int(source_id)},
        )
    _record_entity_redirect(
        conn,
        entity_type,
        source_id,
        target_id,
        created_by=created_by,
        reason="duplicate_merge",
    )


def _execute_aircraft_merge_plan(conn, plan: dict[str, Any], *, created_by: int | None) -> dict[str, Any]:
    source = plan["source"]
    target = plan["target"]
    source_model_id = int(source["model_id"])
    target_model_id = int(target["model_id"])
    for pair in plan["duplicate_flights"]:
        source_flight_id = int(pair["source_flight_id"])
        _delete_server_dynamic_rows(conn, source_model_id, source_flight_id)
        _mark_server_entity_merged(
            conn,
            "flight",
            source_flight_id,
            int(pair["target_flight_id"]),
            created_by=created_by,
        )
    for flight_id in plan["move_flight_ids"]:
        _transfer_server_dynamic_rows(conn, source_model_id, target_model_id, int(flight_id))
        row = conn.execute(db.text("SELECT version FROM flights WHERE id=:id"), {"id": int(flight_id)}).first()
        version = int(row._mapping.get("version") or 1) + 1
        conn.execute(
            db.text(
                """UPDATE flights
                   SET aircraft_id=:aircraft_id, version=:version, updated_at=:updated_at
                   WHERE id=:id"""
            ),
            {
                "aircraft_id": int(target["id"]),
                "version": version,
                "updated_at": db.utcnow(),
                "id": int(flight_id),
            },
        )
        _insert_change_with_type(conn, "flight", int(flight_id), "merge_move", version, "", "")
    _mark_server_entity_merged(
        conn,
        "aircraft",
        int(source["id"]),
        int(target["id"]),
        created_by=created_by,
    )
    _touch_merged_target(conn, "aircraft", int(target["id"]))
    return {
        "source_id": int(source["id"]),
        "target_id": int(target["id"]),
        "moved_flights": len(plan["move_flight_ids"]),
        "merged_flights": len(plan["duplicate_flights"]),
    }


def execute_entity_merge(
    conn,
    entity_type: str,
    source_id: int,
    target_id: int,
    *,
    created_by: int | None,
) -> dict[str, Any]:
    plan = preflight_entity_merge(conn, entity_type, source_id, target_id)
    if not plan["ok"]:
        return plan
    if entity_type == "aircraft":
        result = _execute_aircraft_merge_plan(conn, plan, created_by=created_by)
        return {"ok": True, "status": "success", "entity_type": entity_type, **result}

    source_model_id = int(plan["source_id"])
    target_model_id = int(plan["target_id"])
    aircraft_results = [
        _execute_aircraft_merge_plan(conn, aircraft_plan, created_by=created_by)
        for aircraft_plan in plan["aircraft_merges"]
    ]
    for aircraft_id in plan["move_aircraft_ids"]:
        for flight in _active_aircraft_flights(conn, int(aircraft_id)):
            _transfer_server_dynamic_rows(conn, source_model_id, target_model_id, int(flight["id"]))
        row = conn.execute(db.text("SELECT version FROM aircraft WHERE id=:id"), {"id": int(aircraft_id)}).first()
        version = int(row._mapping.get("version") or 1) + 1
        conn.execute(
            db.text(
                """UPDATE aircraft
                   SET model_id=:model_id, version=:version, updated_at=:updated_at
                   WHERE id=:id"""
            ),
            {
                "model_id": target_model_id,
                "version": version,
                "updated_at": db.utcnow(),
                "id": int(aircraft_id),
            },
        )
        _insert_change_with_type(conn, "aircraft", int(aircraft_id), "merge_move", version, "", "")
    _mark_server_entity_merged(
        conn,
        "model",
        source_model_id,
        target_model_id,
        created_by=created_by,
    )
    _touch_merged_target(conn, "model", target_model_id)
    return {
        "ok": True,
        "status": "success",
        "entity_type": "model",
        "source_id": source_model_id,
        "target_id": target_model_id,
        "moved_aircraft": len(plan["move_aircraft_ids"]),
        "merged_aircraft": aircraft_results,
    }


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
