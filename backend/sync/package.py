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
from typing import Callable

from backend.sync import repository as sync_repository
from backend.sync.cleanup import cleanup_files
from backend.sync.metrics import SyncMetrics
from backend.sync.protocol import (
    PACKAGE_VERSION,
    SYNC_PROTOCOL_VERSION,
    model_structure_signature,
    safe_zip_path as _safe_zip_path,
    sha256_file as _sha256_file,
)
from backend.database import CURRENT_SCHEMA_VERSION, DATA_DIR
from backend.import_pipeline.format_configs import build_model_config_from_db
from backend.raw_storage import RAW_ROOT


APP_VERSION = "2.0.0"
EXPORT_DIR = os.path.join(DATA_DIR, "sync_exports")
ONLINE_CACHE_DIR = os.path.join(DATA_DIR, "sync_cache")
SQLITE_COPY_BATCH_SIZE = 5000


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
    for model in models:
        config = build_model_config_from_db(conn, int(model["id"]))
        model["config_signature"] = model_structure_signature(config)
        model["config"] = config
    aircraft = _rows_by_ids(conn, "aircraft", ids["aircraft"])
    flights = _rows_by_ids(conn, "flights", ids["flights"])

    if ids["flights"]:
        raw_rows = []
        for batch in sync_repository.batched_ids(ids["flights"]):
            placeholders = ",".join("?" for _ in batch)
            raw_rows.extend(conn.execute(
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
                batch,
            ).fetchall())
        raw_rows.sort(key=lambda row: (int(row["flight_id"]), row["original_rel_path"], int(row["id"])))
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


def _selected_ids(
    conn,
    flight_ids: list[int],
    model_ids: list[int] | None,
    aircraft_ids: list[int] | None,
) -> dict[str, set[int]]:
    clean_flight_ids = sorted({int(fid) for fid in flight_ids})
    ids = sync_repository.selected_ids(conn, clean_flight_ids) if clean_flight_ids else {
        "flights": set(),
        "aircraft": set(),
        "models": set(),
    }
    for model_id in model_ids or []:
        ids["models"].add(int(model_id))
    for aircraft_id in aircraft_ids or []:
        row = conn.execute(
            "SELECT id, model_id FROM aircraft WHERE id=?", (int(aircraft_id),)
        ).fetchone()
        if not row:
            raise ValueError(f"飞机不存在: {aircraft_id}")
        ids["aircraft"].add(int(row["id"]))
        ids["models"].add(int(row["model_id"]))
    if not ids["models"] and not ids["aircraft"] and not ids["flights"]:
        raise ValueError("至少选择一个待同步项目")
    return ids


def build_lightweight_manifest(
    conn,
    flight_ids: list[int],
    *,
    model_ids: list[int] | None = None,
    aircraft_ids: list[int] | None = None,
    package_id: str | None = None,
    base_server_cursor: str | None = None,
) -> dict:
    ids = _selected_ids(conn, flight_ids, model_ids, aircraft_ids)
    source_node_id = sync_repository.get_setting(
        conn, "local_node_id", sync_repository.get_setting(conn, "node_id", "field-unknown")
    )
    source_environment = sync_repository.get_setting(conn, "environment", "field")
    if base_server_cursor is None:
        base_server_cursor = sync_repository.get_setting(conn, "last_pull_cursor", "") or None
    manifest = _manifest(
        conn,
        ids,
        source_node_id,
        source_environment,
        "push_batch",
        package_id or f"pkg-{uuid.uuid4().hex}",
        base_server_cursor,
    )
    manifest["preview_only"] = True
    return manifest


def prepare_online_upload(
    conn,
    flight_ids: list[int],
    *,
    model_ids: list[int] | None = None,
    aircraft_ids: list[int] | None = None,
    operation_id: str | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    os.makedirs(ONLINE_CACHE_DIR, exist_ok=True)
    cleanup_files(
        ONLINE_CACHE_DIR,
        max_age_seconds=2 * 24 * 60 * 60,
        suffixes=(".sqlite",),
    )
    ids = _selected_ids(conn, flight_ids, model_ids, aircraft_ids)
    source_node_id = sync_repository.get_setting(
        conn, "local_node_id", sync_repository.get_setting(conn, "node_id", "field-unknown")
    )
    source_environment = sync_repository.get_setting(conn, "environment", "field")
    base_server_cursor = sync_repository.get_setting(conn, "last_pull_cursor", "") or None
    package_id = f"pkg-{uuid.uuid4().hex}"
    metrics = SyncMetrics("client_online_prepare", operation_id)
    with metrics.phase("client_manifest") as phase_metric:
        manifest = _manifest(
            conn,
            ids,
            source_node_id,
            source_environment,
            "push_batch",
            package_id,
            base_server_cursor,
        )
        phase_metric.update(
            models=len(manifest["models"]),
            aircraft=len(manifest["aircraft"]),
            flights=len(manifest["flights"]),
            raw_files=len(manifest["raw_files"]),
        )
    parsed_path = os.path.join(ONLINE_CACHE_DIR, f"upload_{package_id}.sqlite")
    try:
        with metrics.phase("client_parsed_export") as phase_metric:
            parsed_stats = write_parsed_sqlite(
                conn,
                ids,
                parsed_path,
                progress_callback=progress_callback,
            )
            parsed_size = os.path.getsize(parsed_path)
            phase_metric.update(**parsed_stats, bytes=parsed_size)
        with metrics.phase("client_parsed_hash") as phase_metric:
            parsed_sha = _sha256_file(parsed_path)
            phase_metric["bytes"] = parsed_size
        manifest["parsed_data"].update(
            {
                "sha256": parsed_sha,
                "size_bytes": parsed_size,
                "table_rows": parsed_stats["table_rows"],
            }
        )

        raw_objects: dict[str, dict] = {}
        with metrics.phase("client_raw_index") as phase_metric:
            raw_root = os.path.abspath(RAW_ROOT)
            for row in manifest["raw_files"]:
                sha = str(row["sha256"]).lower()
                rel = _safe_zip_path(row["storage_rel_path"])
                path = os.path.abspath(os.path.join(raw_root, *rel.split("/")))
                if os.path.commonpath([path, raw_root]) != raw_root:
                    raise ValueError("raw file path escapes raw storage root")
                if not os.path.exists(path) or os.path.getsize(path) != int(row["size_bytes"]):
                    raise ValueError(f"原始文件缺失或大小不一致: {rel}")
                existing = raw_objects.get(sha)
                if existing and int(existing["size_bytes"]) != int(row["size_bytes"]):
                    raise ValueError(f"原始对象大小冲突: {sha}")
                raw_objects.setdefault(
                    sha,
                    {
                        "kind": "raw",
                        "sha256": sha,
                        "size_bytes": int(row["size_bytes"]),
                        "path": path,
                    },
                )
            phase_metric.update(
                objects=len(raw_objects),
                bytes=sum(int(item["size_bytes"]) for item in raw_objects.values()),
            )
        objects = [
            {
                "kind": "parsed",
                "sha256": parsed_sha,
                "size_bytes": parsed_size,
                "path": parsed_path,
            },
            *raw_objects.values(),
        ]
        metrics.sample_temp(parsed_path)
        return {
            "ok": True,
            "package_id": package_id,
            "manifest": manifest,
            "objects": objects,
            "parsed_path": parsed_path,
            "metrics": metrics.result(),
        }
    except Exception:
        try:
            os.remove(parsed_path)
        except OSError:
            pass
        raise


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


def write_parsed_sqlite(
    conn,
    ids: dict[str, set[int]],
    out_path: str,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    """Write a cropped parsed-data cache with source IDs preserved."""
    if os.path.exists(out_path):
        os.remove(out_path)
    dst = sqlite3.connect(out_path)
    dst.row_factory = sqlite3.Row
    copied_rows = 0
    copied_tables = 0
    table_rows: dict[str, int] = {}
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
            rows = []
            for batch in sync_repository.batched_ids(ids["models"]):
                placeholders = ",".join("?" for _ in batch)
                rows.extend(conn.execute(
                    f"SELECT * FROM {_q(table)} WHERE model_id IN ({placeholders}) ORDER BY id",
                    batch,
                ).fetchall())
            rows.sort(key=lambda row: int(row["id"]))
            _create_source_table(dst, table, [dict(r) for r in rows], {"model_id": "source_model_id"})

        registry = []
        for batch in sync_repository.batched_ids(ids["models"]):
            registry.extend(conn.execute(
                f"""SELECT table_name FROM data_table_registry
                    WHERE model_id IN ({','.join('?' for _ in batch)})
                    ORDER BY table_name""",
                batch,
            ).fetchall())
        registry.sort(key=lambda row: row["table_name"])
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
            copied_tables += 1

            insert_cols = ["source_id"] + [
                ("source_flight_id" if c == "flight_id" else c)
                for c in source_cols
                if c != "id"
            ]
            sql = f"INSERT INTO {_q(source_table)} ({', '.join(_q(c) for c in insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})"
            table_count = 0
            if ids["flights"]:
                for flight_batch in sync_repository.batched_ids(ids["flights"]):
                    placeholders = ",".join("?" for _ in flight_batch)
                    cursor = conn.execute(
                        f"SELECT * FROM {_q(source_table)} WHERE flight_id IN ({placeholders}) ORDER BY flight_id, id",
                        flight_batch,
                    )
                    while data_rows := cursor.fetchmany(SQLITE_COPY_BATCH_SIZE):
                        dst.executemany(
                            sql,
                            [
                                [row["id"]]
                                + [row[column] for column in source_cols if column != "id"]
                                for row in data_rows
                            ],
                        )
                        table_count += len(data_rows)
                        copied_rows += len(data_rows)
                        if progress_callback:
                            progress_callback(
                                {
                                    "phase": "client_parsed_export",
                                    "current": copied_rows,
                                    "unit": "rows",
                                    "table_name": source_table,
                                }
                            )
            table_rows[source_table] = table_count
        dst.commit()
        return {"rows": copied_rows, "tables": copied_tables, "table_rows": table_rows}
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
    operation_id: str | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    """Export selected flights into a .fapkg zip under the fixed export dir."""
    if bundle_kind not in {"manual_export", "push_batch", "pull_bundle"}:
        raise ValueError(f"Unsupported bundle_kind: {bundle_kind}")
    metrics = SyncMetrics("client_export", operation_id)
    ids = _selected_ids(conn, flight_ids, model_ids, aircraft_ids)
    source_node_id = sync_repository.get_setting(
        conn, "local_node_id", sync_repository.get_setting(conn, "node_id", "field-unknown")
    )
    source_environment = sync_repository.get_setting(conn, "environment", "field")
    if base_server_cursor is None:
        base_server_cursor = sync_repository.get_setting(conn, "last_pull_cursor", "") or None
    package_id = package_id or f"pkg-{uuid.uuid4().hex}"
    with metrics.phase("client_manifest") as phase_metric:
        manifest = _manifest(
            conn,
            ids,
            source_node_id,
            source_environment,
            bundle_kind,
            package_id,
            base_server_cursor,
        )
        phase_metric.update(
            models=len(manifest["models"]),
            aircraft=len(manifest["aircraft"]),
            flights=len(manifest["flights"]),
            raw_files=len(manifest["raw_files"]),
        )
        if progress_callback:
            progress_callback(
                {
                    "phase": "client_manifest",
                    "current": sum(
                        len(manifest[key]) for key in ("models", "aircraft", "flights", "raw_files")
                    ),
                    "total": sum(
                        len(manifest[key]) for key in ("models", "aircraft", "flights", "raw_files")
                    ),
                    "unit": "entities",
                }
            )
    out_path = _unique_package_path(source_node_id, bundle_kind)

    tmp_dir = tempfile.mkdtemp(prefix="flightanalyzer_export_")
    parsed_path = os.path.join(tmp_dir, "parsed.sqlite")
    manifest_path = os.path.join(tmp_dir, "manifest.json")
    try:
        with metrics.phase("client_parsed_export") as phase_metric:
            parsed_stats = write_parsed_sqlite(
                conn, ids, parsed_path, progress_callback=progress_callback
            )
            parsed_size = os.path.getsize(parsed_path)
            phase_metric.update(**parsed_stats, bytes=parsed_size)
        with metrics.phase("client_parsed_hash") as phase_metric:
            parsed_sha = _sha256_file(parsed_path)
            phase_metric["bytes"] = parsed_size
        manifest["parsed_data"]["sha256"] = parsed_sha
        manifest["parsed_data"]["size_bytes"] = parsed_size
        manifest["parsed_data"]["table_rows"] = parsed_stats["table_rows"]

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        with metrics.phase("client_raw_validate") as phase_metric:
            raw_bytes = 0
            for index, raw in enumerate(manifest["raw_files"], start=1):
                rel = _safe_zip_path(raw["storage_rel_path"])
                src = os.path.abspath(os.path.join(RAW_ROOT, rel))
                root = os.path.abspath(RAW_ROOT)
                if os.path.commonpath([src, root]) != root:
                    raise ValueError("raw file path escapes raw storage root")
                if not os.path.exists(src):
                    raise ValueError(f"原始文件缺失: {rel}")
                if os.path.getsize(src) != raw["size_bytes"] or _sha256_file(src) != raw["sha256"]:
                    raise ValueError(f"原始文件校验失败: {rel}")
                raw_bytes += int(raw["size_bytes"] or 0)
                if progress_callback:
                    progress_callback(
                        {
                            "phase": "client_raw_validate",
                            "current": index,
                            "total": len(manifest["raw_files"]),
                            "unit": "files",
                            "file_name": rel,
                        }
                    )
            phase_metric.update(files=len(manifest["raw_files"]), bytes=raw_bytes)

        with metrics.phase("client_zip") as phase_metric:
            with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(manifest_path, "manifest.json")
                zf.write(parsed_path, "data/parsed.sqlite")
                for model_id in sorted(ids["models"]):
                    config = build_model_config_from_db(conn, model_id) or {}
                    zf.writestr(
                        f"models/model_{model_id}.json",
                        json.dumps(config, ensure_ascii=False, indent=2),
                    )
                for index, raw in enumerate(manifest["raw_files"], start=1):
                    rel = _safe_zip_path(raw["storage_rel_path"])
                    src = os.path.abspath(os.path.join(RAW_ROOT, rel))
                    zf.write(src, _safe_zip_path(raw["package_path"]))
                    if progress_callback:
                        progress_callback(
                            {
                                "phase": "client_zip",
                                "current": index,
                                "total": len(manifest["raw_files"]),
                                "unit": "files",
                                "file_name": rel,
                            }
                        )
            zip_size = os.path.getsize(out_path)
            phase_metric.update(
                files=len(manifest["raw_files"]) + len(ids["models"]) + 2,
                input_bytes=raw_bytes + parsed_size + os.path.getsize(manifest_path),
                output_bytes=zip_size,
                compression_ratio=round(
                    zip_size / max(1, raw_bytes + parsed_size + os.path.getsize(manifest_path)), 6
                ),
            )
        metrics.sample_temp(tmp_dir, out_path)
        result = {
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
        result["metrics"] = metrics.result()
        return result
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
