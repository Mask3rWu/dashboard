"""Offline sync package import for research nodes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from backend.database import CURRENT_SCHEMA_VERSION
from backend.format_configs import (
    build_model_config_from_db,
    data_table_name,
    register_model_tables,
)
from backend.raw_storage import OBJECT_ROOT
from backend.sync_package import PACKAGE_VERSION


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_RECORD_COLUMNS = (
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
)


def _q(identifier: str) -> str:
    if not _SAFE_IDENTIFIER.match(identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return f'"{identifier}"'


def _safe_zip_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/")
    if raw.startswith("/") or _WINDOWS_DRIVE.match(raw):
        raise ValueError(f"Unsafe zip path: {path}")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Unsafe zip path: {path}")
    return "/".join(parts)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _assert_safe_zip(zf: zipfile.ZipFile) -> None:
    for info in zf.infolist():
        if info.is_dir():
            continue
        _safe_zip_path(info.filename)


def _load_manifest(package_path: str) -> tuple[dict, list[str]]:
    if not os.path.isfile(package_path):
        raise ValueError(f"同步包不存在: {package_path}")
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            _assert_safe_zip(zf)
            if "manifest.json" not in zf.namelist():
                raise ValueError("同步包缺少 manifest.json")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            declared = {"manifest.json"}
            parsed_data = manifest.get("parsed_data")
            if not isinstance(parsed_data, dict):
                parsed_data = {}
            parsed_path = parsed_data.get("path")
            if parsed_path:
                declared.add(_safe_zip_path(parsed_path))
            raw_files = manifest.get("raw_files")
            if not isinstance(raw_files, list):
                raw_files = []
            for raw in raw_files:
                if not isinstance(raw, dict):
                    continue
                if raw.get("package_path"):
                    declared.add(_safe_zip_path(raw["package_path"]))
            extras = [
                name for name in zf.namelist()
                if not name.endswith("/") and name not in declared and not name.startswith("models/")
            ]
            if extras:
                warnings.append(f"同步包包含 {len(extras)} 个未声明文件，已忽略")
            return manifest, warnings
    except zipfile.BadZipFile as e:
        raise ValueError("同步包不是有效 zip/fapkg 文件") from e


def _extract_package(package_path: str) -> str:
    tmp_dir = tempfile.mkdtemp(prefix="flightanalyzer_import_")
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            _assert_safe_zip(zf)
            zf.extractall(tmp_dir)
        return tmp_dir
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _read_model_config_from_zip(package_path: str, source_model_id: int) -> dict | None:
    member = f"models/model_{source_model_id}.json"
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            if member not in zf.namelist():
                return None
            return json.loads(zf.read(member).decode("utf-8"))
    except Exception:
        return None


def _config_signature(config: dict | None) -> dict | None:
    if not config:
        return None
    data_types = {}
    for key, tdef in sorted((config.get("data_types") or {}).items()):
        columns = []
        for col in sorted(tdef.get("columns", []), key=lambda c: (c.get("ordinal") is None, c.get("ordinal") or 0, c.get("name", ""))):
            columns.append({
                "name": col.get("name"),
                "type": (col.get("type") or "REAL").upper(),
                "ordinal": col.get("ordinal"),
            })
        data_types[key] = {
            "is_alert": bool(tdef.get("is_alert")),
            "patterns": sorted(tdef.get("file_patterns") or []),
            "columns": columns,
        }
    return {
        "has_header": bool(config.get("has_header")),
        "has_uav_send_id": bool(config.get("has_uav_send_id")),
        "extract_serial_from_path": bool(config.get("extract_serial_from_path")),
        "data_types": data_types,
    }


def _model_row_by_source(manifest: dict, source_model_id: int) -> dict:
    for row in manifest.get("models", []):
        if int(row.get("id")) == int(source_model_id):
            return row
    return {"id": source_model_id, "name": f"外场机型 {source_model_id}"}


def _existing_models(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name FROM aircraft_models ORDER BY name, id"
    ).fetchall()
    return [dict(r) for r in rows]


def _find_matching_model(conn, source_config: dict | None) -> dict | None:
    source_sig = _config_signature(source_config)
    if not source_sig:
        return None
    for model in _existing_models(conn):
        target_config = build_model_config_from_db(conn, model["id"])
        if _config_signature(target_config) == source_sig:
            return model
    return None


def _unique_name(conn, table: str, base_name: str, where_sql: str = "", params: tuple = ()) -> str:
    name = (base_name or "未命名").strip() or "未命名"
    root = name
    suffix = 1
    while True:
        row = conn.execute(
            f"SELECT id FROM {_q(table)} WHERE name=? {where_sql}",
            (name, *params),
        ).fetchone()
        if not row:
            return name
        name = f"{root} ({suffix})"
        suffix += 1


def _create_model_from_config(conn, name: str, config: dict) -> int:
    if not config or not config.get("data_types"):
        raise ValueError("同步包机型缺少数据类型配置，无法创建")
    safe_name = _unique_name(conn, "aircraft_models", name)
    conn.execute(
        """INSERT INTO aircraft_models
           (name, has_header, has_uav_send_id, extract_serial_from_path)
           VALUES (?, ?, ?, ?)""",
        (
            safe_name,
            1 if config.get("has_header") else 0,
            1 if config.get("has_uav_send_id") else 0,
            1 if config.get("extract_serial_from_path") else 0,
        ),
    )
    model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    register_model_tables(conn, model_id, config=config, commit=False)
    return int(model_id)


def _create_aircraft(conn, model_id: int, name: str) -> int:
    safe_name = _unique_name(conn, "aircraft", name, "AND model_id=?", (model_id,))
    conn.execute(
        "INSERT INTO aircraft (model_id, name) VALUES (?, ?)",
        (model_id, safe_name),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _flight_row_by_source(manifest: dict, source_flight_id: int) -> dict | None:
    for row in manifest.get("flights", []):
        if int(row.get("id")) == int(source_flight_id):
            return row
    return None


def _record_values(row: dict) -> list[Any]:
    return [row.get(column) for column in _RECORD_COLUMNS]


def preview_import(conn, package_path: str) -> dict:
    manifest, warnings = _load_manifest(package_path)
    _validate_manifest(manifest, require_compatible=False)

    source_model_ids = sorted({int(m["id"]) for m in manifest.get("models", [])})
    model_plans = []
    model_target_map: dict[int, int] = {}
    for source_model_id in source_model_ids:
        config = _read_model_config_from_zip(package_path, source_model_id)
        source_row = _model_row_by_source(manifest, source_model_id)
        match = _find_matching_model(conn, config)
        plan = {
            "source_model_id": source_model_id,
            "source_name": source_row.get("name") or f"外场机型 {source_model_id}",
            "matched_model": match,
            "requires_confirmation": match is None,
            "default_action": "use_existing" if match else "create",
            "create_name": source_row.get("name") or f"外场机型 {source_model_id}",
        }
        if match:
            model_target_map[source_model_id] = match["id"]
        model_plans.append(plan)

    aircraft_plans = []
    source_aircraft = manifest.get("aircraft", [])
    for aircraft in source_aircraft:
        source_aircraft_id = int(aircraft["id"])
        source_model_id = int(aircraft["model_id"])
        target_model_id = model_target_map.get(source_model_id)
        existing = None
        choices = []
        if target_model_id:
            rows = conn.execute(
                "SELECT id, name FROM aircraft WHERE model_id=? ORDER BY name, id",
                (target_model_id,),
            ).fetchall()
            choices = [dict(r) for r in rows]
            existing = next((r for r in choices if r["name"] == aircraft.get("name")), None)
        aircraft_plans.append({
            "source_aircraft_id": source_aircraft_id,
            "source_model_id": source_model_id,
            "source_name": aircraft.get("name") or f"外场飞机 {source_aircraft_id}",
            "target_model_id": target_model_id,
            "matched_aircraft": existing,
            "existing_aircraft": choices,
            "requires_mapping": existing is None,
            "default_action": "use_existing" if existing else "create",
            "create_name": aircraft.get("name") or f"外场飞机 {source_aircraft_id}",
        })

    duplicates = _preview_duplicates(conn, manifest, aircraft_plans)
    dates = [f.get("flight_date") for f in manifest.get("flights", []) if f.get("flight_date")]
    compatible = _is_compatible(manifest)
    return {
        "package_path": package_path,
        "summary": {
            "source_node_id": manifest.get("source_node_id"),
            "source_environment": manifest.get("source_environment"),
            "exported_at": manifest.get("exported_at"),
            "flight_count": len(manifest.get("flights", [])),
            "aircraft_count": len(manifest.get("aircraft", [])),
            "model_count": len(manifest.get("models", [])),
            "date_from": min(dates) if dates else None,
            "date_to": max(dates) if dates else None,
            "package_version": manifest.get("package_version"),
            "schema_version": manifest.get("schema_version"),
            "compatible": compatible,
            "import_path": "parsed_sqlite" if compatible else "raw_reparse_required",
        },
        "model_plans": model_plans,
        "aircraft_plans": aircraft_plans,
        "duplicates": duplicates,
        "warnings": warnings,
    }


def _preview_duplicates(conn, manifest: dict, aircraft_plans: list[dict]) -> list[dict]:
    auto_aircraft = {
        p["source_aircraft_id"]: p["matched_aircraft"]["id"]
        for p in aircraft_plans
        if p.get("matched_aircraft")
    }
    duplicates = []
    for flight in manifest.get("flights", []):
        target_aircraft_id = auto_aircraft.get(int(flight["aircraft_id"]))
        if not target_aircraft_id:
            continue
        existing = conn.execute(
            """SELECT id, name FROM flights
               WHERE aircraft_id=? AND flight_date IS ? AND session_key=?""",
            (target_aircraft_id, flight.get("flight_date"), flight.get("session_key") or ""),
        ).fetchone()
        if existing:
            duplicates.append({
                "source_flight_id": flight["id"],
                "source_name": flight.get("name"),
                "target_flight_id": existing["id"],
                "target_name": existing["name"],
                "target_aircraft_id": target_aircraft_id,
            })
    return duplicates


def _is_compatible(manifest: dict) -> bool:
    return (
        int(manifest.get("package_version") or 0) == PACKAGE_VERSION
        and int(manifest.get("schema_version") or 0) == CURRENT_SCHEMA_VERSION
        and manifest.get("parsed_data", {}).get("format") == "sqlite"
    )


def _validate_manifest(manifest: dict, require_compatible: bool) -> None:
    for key in ("package_version", "schema_version", "models", "aircraft", "flights", "raw_files", "parsed_data"):
        if key not in manifest:
            raise ValueError(f"manifest 缺少字段: {key}")
    for key in ("models", "aircraft", "flights", "raw_files"):
        if not isinstance(manifest.get(key), list):
            raise ValueError(f"manifest 字段格式无效: {key}")
        if any(not isinstance(item, dict) for item in manifest.get(key, [])):
            raise ValueError(f"manifest 字段包含无效条目: {key}")
    if not isinstance(manifest.get("parsed_data"), dict):
        raise ValueError("manifest 字段格式无效: parsed_data")
    if require_compatible and not _is_compatible(manifest):
        raise ValueError("当前版本暂不支持该同步包的原始文件重解析导入路径")


def import_package(conn, package_path: str, options: dict | None = None) -> dict:
    options = options or {}
    manifest, package_warnings = _load_manifest(package_path)
    _validate_manifest(manifest, require_compatible=True)

    tmp_dir = _extract_package(package_path)
    report = {
        "package_path": package_path,
        "source_node_id": manifest.get("source_node_id"),
        "status": "running",
        "created_models": [],
        "created_aircraft": [],
        "imported_flights": [],
        "skipped_flights": [],
        "updated_flights": [],
        "conflicts": [],
        "warnings": [{"scope": "package", "message": w} for w in package_warnings],
        "failures": [],
        "raw_files": {"attached": 0, "warnings": 0},
        "parsed_rows": 0,
    }
    try:
        parsed_path = _validated_parsed_sqlite(tmp_dir, manifest)
        model_map = _resolve_models(conn, package_path, manifest, options, report)
        aircraft_map = _resolve_aircraft(conn, manifest, model_map, options, report)
        flight_map, new_source_flight_ids = _import_flights(
            conn, manifest, aircraft_map, options, report
        )
        _import_raw_files(conn, tmp_dir, manifest, flight_map, new_source_flight_ids, report)
        report["parsed_rows"] = _import_parsed_rows(
            conn, parsed_path, manifest, model_map, flight_map, new_source_flight_ids, report
        )
        status = "partial" if report["failures"] or report["warnings"] else "success"
        report["status"] = status
        import_id = _save_report(conn, package_path, manifest, status, report)
        conn.commit()
        report["id"] = import_id
        return report
    except Exception as e:
        conn.rollback()
        report["status"] = "failed"
        report["failures"].append({"scope": "package", "error": str(e)})
        import_id = _save_report(conn, package_path, manifest, "failed", report)
        conn.commit()
        report["id"] = import_id
        return report
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _validated_parsed_sqlite(tmp_dir: str, manifest: dict) -> str:
    parsed = manifest.get("parsed_data") or {}
    rel_path = _safe_zip_path(parsed.get("path") or "data/parsed.sqlite")
    path = os.path.abspath(os.path.join(tmp_dir, rel_path))
    root = os.path.abspath(tmp_dir)
    if os.path.commonpath([path, root]) != root:
        raise ValueError("parsed sqlite path escapes package temp dir")
    if not os.path.exists(path):
        raise ValueError("同步包缺少 parsed.sqlite")
    expected_size = parsed.get("size_bytes")
    expected_sha = parsed.get("sha256")
    if expected_size is not None and os.path.getsize(path) != int(expected_size):
        raise ValueError("parsed.sqlite size 校验失败")
    if expected_sha and _sha256_file(path) != expected_sha:
        raise ValueError("parsed.sqlite sha256 校验失败")
    return path


def _model_actions(options: dict) -> dict[int, dict]:
    return {
        int(item.get("source_model_id")): item
        for item in options.get("model_actions", []) or []
        if item.get("source_model_id") is not None
    }


def _aircraft_actions(options: dict) -> dict[int, dict]:
    return {
        int(item.get("source_aircraft_id")): item
        for item in options.get("aircraft_mappings", []) or []
        if item.get("source_aircraft_id") is not None
    }


def _resolve_models(conn, package_path: str, manifest: dict, options: dict, report: dict) -> dict[int, int]:
    actions = _model_actions(options)
    model_map: dict[int, int] = {}
    for row in manifest.get("models", []):
        source_model_id = int(row["id"])
        config = _read_model_config_from_zip(package_path, source_model_id)
        match = _find_matching_model(conn, config)
        action = actions.get(source_model_id)
        if match and not action:
            model_map[source_model_id] = int(match["id"])
            continue
        if action and action.get("action") == "use_existing":
            target_id = int(action.get("target_model_id") or 0)
            if not conn.execute("SELECT id FROM aircraft_models WHERE id=?", (target_id,)).fetchone():
                raise ValueError(f"目标机型不存在: {target_id}")
            model_map[source_model_id] = target_id
            continue
        if action and action.get("action") == "create":
            name = action.get("name") or row.get("name") or f"外场机型 {source_model_id}"
            target_id = _create_model_from_config(conn, name, config or {})
            model_map[source_model_id] = target_id
            report["created_models"].append({
                "source_model_id": source_model_id,
                "target_model_id": target_id,
                "name": name,
            })
            continue
        if match:
            model_map[source_model_id] = int(match["id"])
            continue
        raise ValueError(f"机型 {row.get('name') or source_model_id} 未确认导入方式")
    return model_map


def _resolve_aircraft(conn, manifest: dict, model_map: dict[int, int], options: dict, report: dict) -> dict[int, int]:
    actions = _aircraft_actions(options)
    aircraft_map: dict[int, int] = {}
    for row in manifest.get("aircraft", []):
        source_aircraft_id = int(row["id"])
        source_model_id = int(row["model_id"])
        target_model_id = model_map[source_model_id]
        name = row.get("name") or f"外场飞机 {source_aircraft_id}"
        existing = conn.execute(
            "SELECT id FROM aircraft WHERE model_id=? AND name=?",
            (target_model_id, name),
        ).fetchone()
        action = actions.get(source_aircraft_id)
        if existing and not action:
            aircraft_map[source_aircraft_id] = int(existing["id"])
            continue
        if action and action.get("action") == "use_existing":
            target_id = int(action.get("target_aircraft_id") or 0)
            if not conn.execute(
                "SELECT id FROM aircraft WHERE id=? AND model_id=?",
                (target_id, target_model_id),
            ).fetchone():
                raise ValueError(f"目标飞机不存在或不属于目标机型: {target_id}")
            aircraft_map[source_aircraft_id] = target_id
            continue
        if action and action.get("action") == "create":
            target_id = _create_aircraft(conn, target_model_id, action.get("name") or name)
            aircraft_map[source_aircraft_id] = target_id
            report["created_aircraft"].append({
                "source_aircraft_id": source_aircraft_id,
                "target_aircraft_id": target_id,
                "name": action.get("name") or name,
            })
            continue
        if existing:
            aircraft_map[source_aircraft_id] = int(existing["id"])
            continue
        raise ValueError(f"飞机 {name} 未确认导入方式")
    return aircraft_map


def _import_flights(conn, manifest: dict, aircraft_map: dict[int, int], options: dict, report: dict) -> tuple[dict[int, int], set[int]]:
    conflict_policy = options.get("conflict_policy") or "skip"
    flight_map: dict[int, int] = {}
    new_source_ids: set[int] = set()
    for row in manifest.get("flights", []):
        source_flight_id = int(row["id"])
        target_aircraft_id = aircraft_map[int(row["aircraft_id"])]
        session_key = row.get("session_key") or ""
        existing = conn.execute(
            """SELECT id, name FROM flights
               WHERE aircraft_id=? AND flight_date IS ? AND session_key=?""",
            (target_aircraft_id, row.get("flight_date"), session_key),
        ).fetchone()
        if existing:
            flight_map[source_flight_id] = int(existing["id"])
            if conflict_policy == "update_records":
                conn.execute(
                    f"""UPDATE flights SET name=?, {', '.join(f'{c}=?' for c in _RECORD_COLUMNS)}
                        WHERE id=?""",
                    [row.get("name") or session_key or f"flight_{source_flight_id}", *_record_values(row), existing["id"]],
                )
                report["updated_flights"].append({
                    "source_flight_id": source_flight_id,
                    "target_flight_id": existing["id"],
                    "name": row.get("name"),
                })
            else:
                report["skipped_flights"].append({
                    "source_flight_id": source_flight_id,
                    "target_flight_id": existing["id"],
                    "reason": "duplicate",
                })
            continue

        columns = [
            "aircraft_id", "name", "source_path", "session_key", "flight_date",
            "start_time", "end_time", "duration_sec", "total_rows", *_RECORD_COLUMNS,
        ]
        values = [
            target_aircraft_id,
            row.get("name") or session_key or f"flight_{source_flight_id}",
            f"sync://{manifest.get('source_node_id') or 'unknown'}/{source_flight_id}",
            session_key,
            row.get("flight_date"),
            row.get("start_time"),
            row.get("end_time"),
            row.get("duration_sec"),
            row.get("total_rows") or 0,
            *_record_values(row),
        ]
        conn.execute(
            f"INSERT INTO flights ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            values,
        )
        target_flight_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        flight_map[source_flight_id] = target_flight_id
        new_source_ids.add(source_flight_id)
        report["imported_flights"].append({
            "source_flight_id": source_flight_id,
            "target_flight_id": target_flight_id,
            "name": row.get("name"),
        })
    return flight_map, new_source_ids


def _import_raw_files(
    conn,
    tmp_dir: str,
    manifest: dict,
    flight_map: dict[int, int],
    new_source_flight_ids: set[int],
    report: dict,
) -> None:
    for raw in manifest.get("raw_files", []):
        source_flight_id = int(raw.get("flight_id"))
        if source_flight_id not in new_source_flight_ids:
            continue
        target_flight_id = flight_map.get(source_flight_id)
        if not target_flight_id:
            continue
        try:
            package_rel = _safe_zip_path(raw.get("package_path") or f"objects/{raw.get('storage_rel_path')}")
            src = os.path.abspath(os.path.join(tmp_dir, package_rel))
            root = os.path.abspath(tmp_dir)
            if os.path.commonpath([src, root]) != root:
                raise ValueError("raw object path escapes package temp dir")
            if not os.path.exists(src):
                raise ValueError("raw object missing")
            expected_size = int(raw.get("size_bytes") or -1)
            expected_sha = raw.get("sha256") or ""
            if os.path.getsize(src) != expected_size or _sha256_file(src) != expected_sha:
                raise ValueError("raw object hash/size mismatch")

            existing = conn.execute(
                "SELECT id FROM file_objects WHERE sha256=?",
                (expected_sha,),
            ).fetchone()
            if existing:
                file_object_id = int(existing["id"])
            else:
                storage_rel = _safe_zip_path(raw.get("storage_rel_path") or f"sha256/{expected_sha[:2]}/{expected_sha}")
                dst = os.path.abspath(os.path.join(OBJECT_ROOT, storage_rel))
                object_root = os.path.abspath(OBJECT_ROOT)
                if os.path.commonpath([dst, object_root]) != object_root:
                    raise ValueError("raw object path escapes object root")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.exists(dst):
                    if os.path.getsize(dst) != expected_size or _sha256_file(dst) != expected_sha:
                        raise ValueError("existing raw object path hash/size mismatch")
                else:
                    shutil.copy2(src, dst)
                conn.execute(
                    """INSERT INTO file_objects (sha256, size_bytes, storage_rel_path)
                       VALUES (?, ?, ?)""",
                    (expected_sha, expected_size, storage_rel),
                )
                file_object_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

            conn.execute(
                """INSERT OR IGNORE INTO flight_raw_files
                   (flight_id, file_object_id, original_name, original_rel_path,
                    data_type_key, source_mtime)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    target_flight_id,
                    file_object_id,
                    raw.get("original_name") or os.path.basename(raw.get("original_rel_path") or expected_sha),
                    raw.get("original_rel_path") or raw.get("original_name") or expected_sha,
                    raw.get("data_type_key"),
                    raw.get("source_mtime"),
                ),
            )
            report["raw_files"]["attached"] += 1
        except Exception as e:
            report["raw_files"]["warnings"] += 1
            warning = {
                "scope": "raw_file",
                "source_flight_id": source_flight_id,
                "file": raw.get("original_rel_path") or raw.get("package_path"),
                "error": str(e),
            }
            report["warnings"].append(warning)
            try:
                flight = conn.execute(
                    "SELECT raw_import_warnings FROM flights WHERE id=?",
                    (target_flight_id,),
                ).fetchone()
                existing = json.loads(flight["raw_import_warnings"] or "[]") if flight else []
                if not isinstance(existing, list):
                    existing = []
                existing.append(warning)
                conn.execute(
                    "UPDATE flights SET raw_import_warnings=? WHERE id=?",
                    (json.dumps(existing, ensure_ascii=False), target_flight_id),
                )
            except Exception:
                pass


def _import_parsed_rows(
    conn,
    parsed_path: str,
    manifest: dict,
    model_map: dict[int, int],
    flight_map: dict[int, int],
    new_source_flight_ids: set[int],
    report: dict,
) -> int:
    if not new_source_flight_ids:
        return 0
    parsed = sqlite3.connect(parsed_path)
    parsed.row_factory = sqlite3.Row
    total_rows = 0
    try:
        registry_rows = parsed.execute(
            "SELECT source_model_id, data_type_key, table_name FROM data_table_registry ORDER BY source_model_id, data_type_key"
        ).fetchall()
        for reg in registry_rows:
            source_model_id = int(reg["source_model_id"])
            target_model_id = model_map.get(source_model_id)
            if not target_model_id:
                continue
            source_table = reg["table_name"]
            if not _SAFE_IDENTIFIER.match(source_table):
                report["warnings"].append({"scope": "parsed", "table": source_table, "error": "unsafe source table name"})
                continue
            target_table = data_table_name(target_model_id, reg["data_type_key"])
            target_cols = {
                r["name"] for r in conn.execute(f"PRAGMA table_info({_q(target_table)})").fetchall()
            }
            source_cols = [
                r["name"] for r in parsed.execute(f"PRAGMA table_info({_q(source_table)})").fetchall()
            ]
            insert_source_cols = [
                c for c in source_cols
                if c not in ("source_id", "source_flight_id") and c in target_cols
            ]
            if "flight_id" not in target_cols or not insert_source_cols:
                continue
            placeholders = ",".join("?" for _ in ["flight_id", *insert_source_cols])
            sql = (
                f"INSERT INTO {_q(target_table)} "
                f"({', '.join(_q(c) for c in ['flight_id', *insert_source_cols])}) "
                f"VALUES ({placeholders})"
            )
            source_placeholders = ",".join("?" for _ in new_source_flight_ids)
            rows = parsed.execute(
                f"SELECT * FROM {_q(source_table)} WHERE source_flight_id IN ({source_placeholders}) ORDER BY source_flight_id, source_id",
                sorted(new_source_flight_ids),
            ).fetchall()
            batch = []
            for row in rows:
                target_flight_id = flight_map.get(int(row["source_flight_id"]))
                if not target_flight_id:
                    continue
                batch.append([target_flight_id, *[row[c] for c in insert_source_cols]])
            if batch:
                conn.executemany(sql, batch)
                total_rows += len(batch)
    finally:
        parsed.close()
    return total_rows


def _save_report(conn, package_path: str, manifest: dict, status: str, report: dict) -> int:
    conn.execute(
        """INSERT INTO sync_imports (package_path, source_node_id, status, report_json)
           VALUES (?, ?, ?, ?)""",
        (
            package_path,
            manifest.get("source_node_id"),
            status,
            json.dumps(report, ensure_ascii=False),
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def get_import_report(conn, import_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM sync_imports WHERE id=?",
        (import_id,),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        data["report"] = json.loads(data.pop("report_json") or "{}")
    except Exception:
        data["report"] = {}
    return data
