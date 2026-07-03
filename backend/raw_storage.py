"""Content-addressed storage for imported raw flight files."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from backend.database import DATA_DIR


OBJECT_ROOT = os.path.join(DATA_DIR, "objects")
MANIFEST_ROOT = os.path.join(DATA_DIR, "manifests", "flights")


def _rel_to_posix(path: str) -> str:
    return Path(path).as_posix()


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


def _object_rel_path(sha256: str, source_path: str) -> str:
    suffix = Path(source_path).suffix.lower()
    return _rel_to_posix(Path("sha256") / sha256[:2] / f"{sha256}{suffix}")


def _copy_object(source_path: str, object_abs_path: str, expected_sha256: str) -> None:
    os.makedirs(os.path.dirname(object_abs_path), exist_ok=True)
    if os.path.exists(object_abs_path):
        return

    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp_", suffix=".object", dir=os.path.dirname(object_abs_path)
    )
    os.close(fd)
    try:
        shutil.copy2(source_path, tmp_path)
        actual_sha256, _size = hash_file(tmp_path)
        if actual_sha256 != expected_sha256:
            raise ValueError("raw file hash changed while copying")
        os.replace(tmp_path, object_abs_path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def store_file(conn, path: str) -> int:
    """Store a raw file by content hash and return file_objects.id."""
    sha256, size = hash_file(path)
    existing = conn.execute(
        "SELECT id FROM file_objects WHERE sha256=?", (sha256,)
    ).fetchone()
    if existing:
        return existing["id"]

    storage_rel_path = _object_rel_path(sha256, path)
    object_abs_path = os.path.join(OBJECT_ROOT, storage_rel_path)
    _copy_object(path, object_abs_path, sha256)

    try:
        conn.execute(
            """INSERT INTO file_objects (sha256, size_bytes, storage_rel_path)
               VALUES (?, ?, ?)""",
            (sha256, size, storage_rel_path),
        )
    except Exception:
        existing = conn.execute(
            "SELECT id FROM file_objects WHERE sha256=?", (sha256,)
        ).fetchone()
        if existing:
            return existing["id"]
        raise
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def attach_raw_files_to_flight(
    conn,
    flight_id: int,
    source_root: str,
    files_info: list[dict],
) -> dict:
    """Attach scanned source files to a flight, deduping stored objects.

    Copy or metadata failures are returned as warnings so parsed import can
    succeed even when raw archival is incomplete.
    """
    source_root = os.path.normpath(source_root)
    attached = 0
    warnings = []

    for info in files_info:
        filepath = os.path.normpath(info.get("filepath", ""))
        if not filepath:
            continue
        try:
            file_object_id = store_file(conn, filepath)
            original_rel_path = _rel_to_posix(os.path.relpath(filepath, source_root))
            source_mtime = os.path.getmtime(filepath)
            conn.execute(
                """INSERT OR IGNORE INTO flight_raw_files
                   (flight_id, file_object_id, original_name, original_rel_path,
                    data_type_key, source_mtime)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    flight_id,
                    file_object_id,
                    info.get("filename") or os.path.basename(filepath),
                    original_rel_path,
                    info.get("data_type_key"),
                    source_mtime,
                ),
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
    rows = conn.execute(
        """SELECT frf.id, frf.flight_id, frf.original_name, frf.original_rel_path,
                  frf.data_type_key, frf.source_mtime, frf.created_at,
                  fo.id as file_object_id, fo.sha256, fo.size_bytes,
                  fo.storage_rel_path
           FROM flight_raw_files frf
           JOIN file_objects fo ON fo.id = frf.file_object_id
           WHERE frf.flight_id=?
           ORDER BY frf.original_rel_path, frf.id""",
        (flight_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def build_flight_manifest(conn, flight_id: int) -> dict:
    """Build a logical manifest using current business names."""
    flight = conn.execute(
        """SELECT f.id, f.name, f.session_key, f.flight_date, f.raw_import_warnings,
                  a.id as aircraft_id, a.name as aircraft_name,
                  am.id as model_id, am.name as model_name
           FROM flights f
           JOIN aircraft a ON a.id = f.aircraft_id
           JOIN aircraft_models am ON am.id = a.model_id
           WHERE f.id=?""",
        (flight_id,),
    ).fetchone()
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
