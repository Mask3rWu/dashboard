"""Offline sync package export for field nodes."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime

from backend.sync import repository as sync_repository
from backend.sync.protocol import (
    PACKAGE_VERSION,
    SYNC_PROTOCOL_VERSION,
    safe_zip_path as _safe_zip_path,
    sha256_file as _sha256_file,
)
from backend.database import CURRENT_SCHEMA_VERSION, DATA_DIR
from backend.import_pipeline.format_configs import build_model_config_from_db
from backend.raw_storage import RAW_ROOT


APP_VERSION = "2.0.0"
EXPORT_DIR = os.path.join(DATA_DIR, "sync_exports")


def _q(identifier: str) -> str:
    value = str(identifier or "")
    if not value or "\x00" in value:
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return f'"{value.replace(chr(34), chr(34) + chr(34))}"'


def _dict(row) -> dict:
    return dict(row) if row is not None else {}


def _unique_package_path(source_node_id: str, bundle_kind: str) -> str:
    os.makedirs(EXPORT_DIR, exist_ok=True)
    safe_node = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_node_id or "unknown")
    safe_kind = re.sub(r"[^A-Za-z0-9_.-]+", "_", bundle_kind or "bundle")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"FlightAnalyzer_{safe_kind}_{safe_node}_{stamp}"
    candidate = os.path.join(EXPORT_DIR, f"{base}.fapkg")
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(EXPORT_DIR, f"{base}_{counter}.fapkg")
        counter += 1
    return candidate


def _rows_by_ids(conn, table: str, ids: set[int]) -> list[dict]:
    return sync_repository.rows_by_ids(conn, _q, table, ids)


def _manifest(
    conn,
    ids: dict[str, set[int]],
    source_node_id: str,
    source_environment: str,
    bundle_kind: str,
    package_id: str,
    base_server_cursor: str | None,
) -> dict:
    models = _rows_by_ids(conn, "aircraft_models", ids["models"])
    aircraft = _rows_by_ids(conn, "aircraft", ids["aircraft"])
    flights = _rows_by_ids(conn, "flights", ids["flights"])

    if ids["flights"]:
        placeholders = ",".join("?" for _ in ids["flights"])
        raw_rows = conn.execute(
            f"""SELECT frf.id, frf.client_uid, frf.server_id, frf.source_node_id,
                      frf.sync_origin, frf.sync_state, frf.server_version,
                      frf.last_sync_at, frf.sync_error_json,
                      frf.flight_id, frf.original_name, frf.original_rel_path,
                      frf.storage_rel_path, frf.sha256, frf.size_bytes,
                      frf.data_type_key, frf.source_mtime, frf.created_at,
                      frf.updated_at, frf.deleted_at, frf.server_deleted_at
                FROM flight_raw_files frf
                WHERE frf.flight_id IN ({placeholders})
                ORDER BY frf.flight_id, frf.original_rel_path, frf.id""",
            sorted(ids["flights"]),
        ).fetchall()
    else:
        raw_rows = []
    raw_files = []
    for row in raw_rows:
        item = dict(row)
        storage_rel_path = _safe_zip_path(item["storage_rel_path"])
        item["storage_rel_path"] = storage_rel_path
        item["package_path"] = _safe_zip_path(f"raw_files/{storage_rel_path}")
        raw_files.append(item)

    return {
        "package_version": PACKAGE_VERSION,
        "sync_protocol_version": SYNC_PROTOCOL_VERSION,
        "package_id": package_id,
        "bundle_kind": bundle_kind,
        "app_version": APP_VERSION,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "source_node_id": source_node_id,
        "source_environment": source_environment,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "base_server_cursor": base_server_cursor,
        "models": models,
        "aircraft": aircraft,
        "flights": flights,
        "raw_files": raw_files,
        "parsed_data": {"format": "sqlite", "path": "data/parsed.sqlite"},
    }


def _create_source_table(dst, table: str, rows: list[dict], source_fk_map: dict[str, str] | None = None) -> None:
    source_fk_map = source_fk_map or {}
    if not rows:
        return
    keys = list(rows[0].keys())
    cols = ["source_id INTEGER"]
    for key in keys:
        if key == "id":
            continue
        out_key = source_fk_map.get(key, key)
        value = next((r[key] for r in rows if r.get(key) is not None), None)
        if isinstance(value, int):
            col_type = "INTEGER"
        elif isinstance(value, float):
            col_type = "REAL"
        else:
            col_type = "TEXT"
        cols.append(f"{_q(out_key)} {col_type}")
    dst.execute(f"CREATE TABLE {_q(table)} ({', '.join(cols)})")
    insert_cols = ["source_id"] + [source_fk_map.get(k, k) for k in keys if k != "id"]
    placeholders = ",".join("?" for _ in insert_cols)
    dst.executemany(
        f"INSERT INTO {_q(table)} ({', '.join(_q(c) for c in insert_cols)}) VALUES ({placeholders})",
        [
            [row.get("id")] + [row.get(k) for k in keys if k != "id"]
            for row in rows
        ],
    )


def write_parsed_sqlite(conn, ids: dict[str, set[int]], out_path: str) -> None:
    """Write a cropped parsed-data cache with source IDs preserved."""
    if os.path.exists(out_path):
        os.remove(out_path)
    dst = sqlite3.connect(out_path)
    dst.row_factory = sqlite3.Row
    try:
        _create_source_table(dst, "aircraft_models", _rows_by_ids(conn, "aircraft_models", ids["models"]))
        _create_source_table(
            dst,
            "aircraft",
            _rows_by_ids(conn, "aircraft", ids["aircraft"]),
            {"model_id": "source_model_id"},
        )
        _create_source_table(
            dst,
            "flights",
            _rows_by_ids(conn, "flights", ids["flights"]),
            {"aircraft_id": "source_aircraft_id"},
        )

        for table in ("data_table_registry", "column_registry"):
            placeholders = ",".join("?" for _ in ids["models"])
            rows = conn.execute(
                f"SELECT * FROM {_q(table)} WHERE model_id IN ({placeholders}) ORDER BY id",
                sorted(ids["models"]),
            ).fetchall()
            _create_source_table(dst, table, [dict(r) for r in rows], {"model_id": "source_model_id"})

        registry = conn.execute(
            f"""SELECT table_name FROM data_table_registry
                WHERE model_id IN ({','.join('?' for _ in ids['models'])})
                ORDER BY table_name""",
            sorted(ids["models"]),
        ).fetchall()
        for row in registry:
            source_table = row["table_name"]
            _q(source_table)
            pragma = conn.execute(f"PRAGMA table_info({_q(source_table)})").fetchall()
            if not pragma:
                continue
            source_cols = [p["name"] for p in pragma]
            out_cols = ["source_id INTEGER"]
            for p in pragma:
                name = p["name"]
                if name == "id":
                    continue
                out_name = "source_flight_id" if name == "flight_id" else name
                out_cols.append(f"{_q(out_name)} {p['type'] or 'TEXT'}")
            dst.execute(f"CREATE TABLE {_q(source_table)} ({', '.join(out_cols)})")

            if ids["flights"]:
                placeholders = ",".join("?" for _ in ids["flights"])
                data_rows = conn.execute(
                    f"SELECT * FROM {_q(source_table)} WHERE flight_id IN ({placeholders}) ORDER BY flight_id, id",
                    sorted(ids["flights"]),
                ).fetchall()
            else:
                data_rows = []
            insert_cols = ["source_id"] + [
                ("source_flight_id" if c == "flight_id" else c)
                for c in source_cols
                if c != "id"
            ]
            sql = f"INSERT INTO {_q(source_table)} ({', '.join(_q(c) for c in insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})"
            dst.executemany(
                sql,
                [[r["id"]] + [r[c] for c in source_cols if c != "id"] for r in data_rows],
            )
        dst.commit()
    finally:
        dst.close()


def export_package(
    conn,
    flight_ids: list[int],
    *,
    model_ids: list[int] | None = None,
    aircraft_ids: list[int] | None = None,
    bundle_kind: str = "manual_export",
    package_id: str | None = None,
    base_server_cursor: str | None = None,
) -> dict:
    """Export selected flights into a .fapkg zip under the fixed export dir."""
    if bundle_kind not in {"manual_export", "push_batch", "pull_bundle"}:
        raise ValueError(f"Unsupported bundle_kind: {bundle_kind}")
    clean_flight_ids = sorted({int(fid) for fid in flight_ids})
    ids = sync_repository.selected_ids(conn, clean_flight_ids) if clean_flight_ids else {
        "flights": set(),
        "aircraft": set(),
        "models": set(),
    }
    for model_id in model_ids or []:
        ids["models"].add(int(model_id))
    for aircraft_id in aircraft_ids or []:
        row = conn.execute("SELECT id, model_id FROM aircraft WHERE id=?", (int(aircraft_id),)).fetchone()
        if not row:
            raise ValueError(f"飞机不存在: {aircraft_id}")
        ids["aircraft"].add(int(row["id"]))
        ids["models"].add(int(row["model_id"]))
    if not ids["models"] and not ids["aircraft"] and not ids["flights"]:
        raise ValueError("至少选择一个待同步项目")
    source_node_id = sync_repository.get_setting(
        conn, "local_node_id", sync_repository.get_setting(conn, "node_id", "field-unknown")
    )
    source_environment = sync_repository.get_setting(conn, "environment", "field")
    if base_server_cursor is None:
        base_server_cursor = sync_repository.get_setting(conn, "last_pull_cursor", "") or None
    package_id = package_id or f"pkg-{uuid.uuid4().hex}"
    manifest = _manifest(
        conn,
        ids,
        source_node_id,
        source_environment,
        bundle_kind,
        package_id,
        base_server_cursor,
    )
    out_path = _unique_package_path(source_node_id, bundle_kind)

    tmp_dir = tempfile.mkdtemp(prefix="flightanalyzer_export_")
    parsed_path = os.path.join(tmp_dir, "parsed.sqlite")
    manifest_path = os.path.join(tmp_dir, "manifest.json")
    try:
        write_parsed_sqlite(conn, ids, parsed_path)
        parsed_sha = _sha256_file(parsed_path)
        manifest["parsed_data"]["sha256"] = parsed_sha
        manifest["parsed_data"]["size_bytes"] = os.path.getsize(parsed_path)

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(manifest_path, "manifest.json")
            zf.write(parsed_path, "data/parsed.sqlite")
            for model_id in sorted(ids["models"]):
                config = build_model_config_from_db(conn, model_id) or {}
                zf.writestr(
                    f"models/model_{model_id}.json",
                    json.dumps(config, ensure_ascii=False, indent=2),
                )
            for raw in manifest["raw_files"]:
                rel = _safe_zip_path(raw["storage_rel_path"])
                src = os.path.abspath(os.path.join(RAW_ROOT, rel))
                root = os.path.abspath(RAW_ROOT)
                if os.path.commonpath([src, root]) != root:
                    raise ValueError("raw file path escapes raw storage root")
                if not os.path.exists(src):
                    raise ValueError(f"原始文件缺失: {rel}")
                if os.path.getsize(src) != raw["size_bytes"] or _sha256_file(src) != raw["sha256"]:
                    raise ValueError(f"原始文件校验失败: {rel}")
                zf.write(src, _safe_zip_path(raw["package_path"]))
        return {
            "ok": True,
            "path": out_path,
            "filename": os.path.basename(out_path),
            "package_id": package_id,
            "bundle_kind": bundle_kind,
            "flight_count": len(ids["flights"]),
            "raw_file_count": len(manifest["raw_files"]),
            "parsed_sha256": parsed_sha,
            "parsed_size_bytes": manifest["parsed_data"]["size_bytes"],
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
