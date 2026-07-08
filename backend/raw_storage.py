"""Readable on-disk storage for imported raw flight files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from backend import raw_file_repository
from backend.database import DATA_DIR


RAW_ROOT = os.path.join(DATA_DIR, "raw_files")
MANIFEST_ROOT = os.path.join(DATA_DIR, "manifests", "flights")

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
_WINDOWS_INVALID_CHARS = set('<>:"\\|?*')


def _rel_to_posix(path: str | os.PathLike) -> str:
    return PurePosixPath(str(path).replace("\\", "/")).as_posix()


def hash_file(path: str) -> tuple[str, int]:
    """Return the sha256 hex digest and size for a file."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _safe_part(value: str, fallback: str = "_") -> str:
    cleaned = []
    for char in str(value or "").strip():
        if ord(char) < 32 or char in _WINDOWS_INVALID_CHARS:
            cleaned.append("_")
        else:
            cleaned.append(char)
    text = "".join(cleaned).strip(" .")
    if not text or text in {".", ".."}:
        text = fallback
    if text.upper() in _WINDOWS_RESERVED_NAMES:
        text = f"{text}_"
    return text


def _safe_rel_parts(path: str) -> list[str]:
    text = str(path or "").replace("\\", "/")
    parts = []
    for part in text.split("/"):
        if not part or part == ".":
            continue
        parts.append(_safe_part(part))
    return parts


def _date_prefix(flight_date: str | None) -> str:
    digits = re.sub(r"\D+", "", str(flight_date or ""))
    return digits[:8] if len(digits) >= 8 else "undated"


def _prefix_filename(filename: str, date_prefix: str) -> str:
    safe = _safe_part(filename, "raw_file")
    if safe.startswith(f"{date_prefix}_"):
        return safe
    return f"{date_prefix}_{safe}"


def _flight_base_rel(flight: dict) -> str:
    date = _date_prefix(flight.get("flight_date"))
    model = f"{_safe_part(flight.get('model_name'), 'model')}__model_{flight['model_id']}"
    aircraft = f"{_safe_part(flight.get('aircraft_name'), 'aircraft')}__aircraft_{flight['aircraft_id']}"
    flight_name = _safe_part(flight.get("name") or flight.get("flight_name") or flight.get("session_key"), "flight")
    flight_id = flight.get("flight_id") or flight["id"]
    flight_dir = f"{date}_{flight_name}__flight_{flight_id}"
    return _rel_to_posix(PurePosixPath(model, aircraft, flight_dir))


def _raw_file_rel_path(flight: dict, original_name: str, original_rel_path: str) -> str:
    date = _date_prefix(flight.get("flight_date"))
    parts = _safe_rel_parts(original_rel_path or original_name)
    if not parts:
        parts = [_safe_part(original_name, "raw_file")]
    parts[-1] = _prefix_filename(parts[-1], date)
    return _rel_to_posix(PurePosixPath(_flight_base_rel(flight), *parts))


def _abs_raw_path(storage_rel_path: str) -> str:
    root = os.path.abspath(RAW_ROOT)
    abs_path = os.path.abspath(os.path.join(root, storage_rel_path))
    if os.path.commonpath([root, abs_path]) != root:
        raise ValueError("raw file path escapes raw storage root")
    return abs_path


def _unique_storage_rel_path(conn, desired_rel: str, flight_id: int, raw_file_id: int | None = None) -> str:
    path = PurePosixPath(desired_rel)
    suffix = path.suffix
    stem = path.name[:-len(suffix)] if suffix else path.name
    parent = path.parent
    index = 0
    while True:
        name = path.name if index == 0 else f"{stem}__{index}{suffix}"
        candidate = name if str(parent) == "." else (parent / name).as_posix()
        params: list = [candidate, flight_id]
        sql = "SELECT id FROM flight_raw_files WHERE storage_rel_path=? AND flight_id=?"
        if raw_file_id is not None:
            sql += " AND id<>?"
            params.append(raw_file_id)
        exists = conn.execute(sql, tuple(params)).fetchone()
        if not exists and not os.path.exists(_abs_raw_path(candidate)):
            return candidate
        if raw_file_id is not None and exists and int(exists["id"]) == raw_file_id:
            return candidate
        index += 1


def _copy_verified(source_path: str, destination: str, expected_sha: str, expected_size: int) -> None:
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".raw", dir=os.path.dirname(destination))
    os.close(fd)
    try:
        shutil.copy2(source_path, tmp_path)
        actual_sha, actual_size = hash_file(tmp_path)
        if actual_sha != expected_sha or actual_size != expected_size:
            raise ValueError("raw file hash/size changed while copying")
        os.replace(tmp_path, destination)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _flight_row(conn, flight_id: int) -> dict | None:
    row = raw_file_repository.get_flight_manifest_row(conn, flight_id)
    return dict(row) if row else None


def store_raw_file_for_flight(
    conn,
    flight_id: int,
    source_path: str,
    original_name: str,
    original_rel_path: str,
    data_type_key: str | None = None,
    source_mtime: float | None = None,
    *,
    expected_sha: str | None = None,
    expected_size: int | None = None,
    sync_values: dict | None = None,
) -> int:
    """Copy one raw file into the readable archive and insert its DB row."""
    flight = _flight_row(conn, flight_id)
    if not flight:
        raise ValueError(f"Flight not found: {flight_id}")

    actual_sha, actual_size = hash_file(source_path)
    if expected_sha and actual_sha.lower() != str(expected_sha).lower():
        raise ValueError("raw file sha256 mismatch")
    if expected_size is not None and actual_size != int(expected_size):
        raise ValueError("raw file size mismatch")

    desired_rel = _raw_file_rel_path(flight, original_name, original_rel_path)
    storage_rel_path = _unique_storage_rel_path(conn, desired_rel, flight_id)
    dst = _abs_raw_path(storage_rel_path)
    _copy_verified(source_path, dst, actual_sha, actual_size)

    if sync_values:
        conn.execute(
            """INSERT OR IGNORE INTO flight_raw_files
               (server_id, source_node_id, sync_origin, sync_state, server_version,
                last_sync_at, flight_id, original_name, original_rel_path,
                storage_rel_path, sha256, size_bytes, data_type_key, source_mtime)
               VALUES (?, ?, ?, ?, ?, datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sync_values.get("server_id"),
                sync_values.get("source_node_id"),
                sync_values.get("sync_origin", "package"),
                sync_values.get("sync_state", "local_only"),
                sync_values.get("server_version"),
                flight_id,
                original_name,
                original_rel_path,
                storage_rel_path,
                actual_sha,
                actual_size,
                data_type_key,
                source_mtime,
            ),
        )
    else:
        raw_file_repository.attach_raw_file(
            conn,
            flight_id,
            original_name,
            original_rel_path,
            storage_rel_path,
            actual_sha,
            actual_size,
            data_type_key,
            source_mtime,
        )
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    return int(row[0]) if row else 0


def attach_raw_files_to_flight(
    conn,
    flight_id: int,
    source_root: str,
    files_info: list[dict],
) -> dict:
    """Copy scanned source files into the readable raw archive."""
    source_root = os.path.normpath(source_root)
    attached = 0
    warnings = []

    for info in files_info:
        filepath = os.path.normpath(info.get("filepath", ""))
        if not filepath:
            continue
        try:
            original_rel_path = _rel_to_posix(os.path.relpath(filepath, source_root))
            store_raw_file_for_flight(
                conn,
                flight_id,
                filepath,
                info.get("filename") or os.path.basename(filepath),
                original_rel_path,
                info.get("data_type_key"),
                os.path.getmtime(filepath),
            )
            attached += 1
        except Exception as e:
            warnings.append(
                {
                    "file": info.get("filename") or os.path.basename(filepath),
                    "path": filepath,
                    "error": str(e),
                }
            )

    return {"attached": attached, "warnings": warnings}


def get_raw_files_for_flight(conn, flight_id: int) -> list[dict]:
    return raw_file_repository.get_raw_files_for_flight(conn, flight_id)


def get_raw_directory_for_flight(conn, flight_id: int) -> dict:
    flight = _flight_row(conn, flight_id)
    if not flight:
        return {}
    return {
        "flight_id": flight_id,
        "file_count": len(get_raw_files_for_flight(conn, flight_id)),
        "path": _abs_raw_path(_flight_base_rel(flight)),
        "warnings": refresh_raw_storage_paths(conn, flight_id=flight_id),
    }


def refresh_raw_storage_paths(
    conn,
    *,
    model_id: int | None = None,
    aircraft_id: int | None = None,
    flight_id: int | None = None,
) -> list[dict]:
    """Move raw files to paths implied by current model/aircraft/flight names."""
    where = []
    params = []
    if model_id is not None:
        where.append("am.id=?")
        params.append(model_id)
    if aircraft_id is not None:
        where.append("a.id=?")
        params.append(aircraft_id)
    if flight_id is not None:
        where.append("f.id=?")
        params.append(flight_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = raw_file_repository.get_raw_file_context_rows(conn, where_sql, tuple(params))
    warnings = []
    for row in rows:
        desired = _raw_file_rel_path(row, row["original_name"], row["original_rel_path"])
        if desired == row["storage_rel_path"]:
            continue
        src = _abs_raw_path(row["storage_rel_path"])
        if not os.path.exists(src):
            warnings.append({
                "file": row.get("original_rel_path") or row.get("original_name"),
                "error": f"stored raw file missing: {row['storage_rel_path']}",
            })
            continue
        desired = _unique_storage_rel_path(conn, desired, int(row["flight_id"]), int(row["id"]))
        dst = _abs_raw_path(desired)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            if os.path.exists(dst):
                actual_sha, actual_size = hash_file(dst)
                if actual_sha != row["sha256"] or actual_size != int(row["size_bytes"]):
                    desired = _unique_storage_rel_path(conn, desired, int(row["flight_id"]), int(row["id"]))
                    dst = _abs_raw_path(desired)
            shutil.move(src, dst)
            conn.execute(
                "UPDATE flight_raw_files SET storage_rel_path=? WHERE id=?",
                (desired, row["id"]),
            )
        except Exception as exc:
            warnings.append({
                "file": row.get("original_rel_path") or row.get("original_name"),
                "error": str(exc),
            })
    return warnings


def build_flight_manifest(conn, flight_id: int) -> dict:
    """Build a logical manifest using current business names."""
    flight = raw_file_repository.get_flight_manifest_row(conn, flight_id)
    if not flight:
        return {}

    logical_prefix = _rel_to_posix(
        Path(flight["model_name"]) / flight["aircraft_name"] / flight["name"]
    )
    files = []
    for raw in get_raw_files_for_flight(conn, flight_id):
        files.append(
            {
                **raw,
                "logical_rel_path": _rel_to_posix(
                    Path(logical_prefix) / raw["original_name"]
                ),
            }
        )

    try:
        warnings = json.loads(flight["raw_import_warnings"] or "[]")
        if not isinstance(warnings, list):
            warnings = []
    except Exception:
        warnings = []

    manifest = {
        "flight": dict(flight),
        "logical_prefix": logical_prefix,
        "files": files,
        "warnings": warnings,
    }

    os.makedirs(MANIFEST_ROOT, exist_ok=True)
    manifest_path = os.path.join(MANIFEST_ROOT, f"flight_{flight_id}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    manifest["manifest_path"] = manifest_path
    return manifest
