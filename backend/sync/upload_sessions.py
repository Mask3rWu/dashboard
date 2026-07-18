"""Persistent resumable upload sessions and content-addressed raw objects."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import timedelta
from typing import Any

from backend import server_database as db


CHUNK_SIZE = 8 * 1024 * 1024
SESSION_TTL = timedelta(days=2)
ORPHAN_OBJECT_TTL = timedelta(days=7)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KINDS = {"raw", "parsed"}


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(manifest)).hexdigest()


def manifest_resume_key(manifest: dict[str, Any]) -> str:
    stable = dict(manifest)
    for key in ("package_id", "exported_at", "base_server_cursor", "preview_only"):
        stable.pop(key, None)
    return hashlib.sha256(_canonical_json(stable)).hexdigest()


def raw_object_rel_path(sha256: str) -> str:
    sha = str(sha256 or "").lower()
    if not _SHA256_RE.fullmatch(sha):
        raise ValueError("Invalid raw object SHA256")
    return f"objects/{sha[:2]}/{sha[2:4]}/{sha}"


def raw_object_abs_path(sha256: str) -> str:
    return os.path.abspath(
        os.path.join(db.SERVER_DATA_DIR, "raw_files", *raw_object_rel_path(sha256).split("/"))
    )


def _session_root(session_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", str(session_id or "")):
        raise ValueError("Invalid upload session ID")
    return os.path.abspath(os.path.join(db.SERVER_DATA_DIR, "upload_sessions", session_id))


def _chunk_path(session_id: str, kind: str, sha256: str, chunk_index: int) -> str:
    if kind not in _KINDS or not _SHA256_RE.fullmatch(sha256):
        raise ValueError("Invalid upload object identity")
    return os.path.join(_session_root(session_id), f"{kind}_{sha256}", f"{chunk_index}.part")


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def cleanup_expired(conn) -> dict[str, int]:
    now = db.utcnow()
    rows = conn.execute(
        db.text("SELECT session_id FROM sync_upload_sessions WHERE expires_at < :now"),
        {"now": now},
    ).fetchall()
    session_ids = [str(row._mapping["session_id"]) for row in rows]
    for session_id in session_ids:
        conn.execute(
            db.text("DELETE FROM sync_upload_sessions WHERE session_id=:session_id"),
            {"session_id": session_id},
        )
        shutil.rmtree(_session_root(session_id), ignore_errors=True)

    orphan_rows = conn.execute(
        db.text(
            """SELECT object.sha256, object.storage_rel_path
               FROM raw_objects object
               LEFT JOIN flight_raw_files link ON link.sha256=object.sha256
               LEFT JOIN sync_upload_objects upload
                 ON upload.object_kind='raw' AND upload.sha256=object.sha256
               WHERE link.id IS NULL AND upload.session_id IS NULL
                 AND object.created_at < :cutoff"""
        ),
        {"cutoff": now - ORPHAN_OBJECT_TTL},
    ).fetchall()
    removed_objects = 0
    for row in orphan_rows:
        sha = str(row._mapping["sha256"])
        try:
            os.remove(raw_object_abs_path(sha))
        except FileNotFoundError:
            pass
        conn.execute(db.text("DELETE FROM raw_objects WHERE sha256=:sha"), {"sha": sha})
        removed_objects += 1
    return {"expired_sessions": len(session_ids), "orphan_objects": removed_objects}


def _raw_object_exists(conn, sha256: str, size_bytes: int) -> bool:
    row = conn.execute(
        db.text("SELECT size_bytes FROM raw_objects WHERE sha256=:sha"),
        {"sha": sha256},
    ).first()
    if not row:
        return False
    if int(row._mapping["size_bytes"]) != int(size_bytes):
        raise ValueError(f"Raw object size conflict for {sha256}")
    path = raw_object_abs_path(sha256)
    if not os.path.exists(path) or os.path.getsize(path) != int(size_bytes):
        raise ValueError(f"Raw object storage is missing or corrupt: {sha256}")
    return True


def raw_object_exists(conn, sha256: str, size_bytes: int) -> bool:
    return _raw_object_exists(conn, sha256, size_bytes)


def _manifest_objects(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw_objects: dict[str, int] = {}
    for row in manifest.get("raw_files") or []:
        sha = str(row.get("sha256") or "").lower()
        raw_size = row.get("size_bytes")
        size = int(raw_size) if raw_size is not None else -1
        if not _SHA256_RE.fullmatch(sha) or size < 0:
            raise ValueError("Manifest contains an invalid raw object")
        previous = raw_objects.setdefault(sha, size)
        if previous != size:
            raise ValueError(f"Manifest has conflicting sizes for raw object {sha}")
    parsed = manifest.get("parsed_data") or {}
    parsed_sha = str(parsed.get("sha256") or "").lower()
    raw_parsed_size = parsed.get("size_bytes")
    parsed_size = int(raw_parsed_size) if raw_parsed_size is not None else -1
    if not _SHA256_RE.fullmatch(parsed_sha) or parsed_size < 0:
        raise ValueError("Manifest parsed_data requires sha256 and size_bytes")
    return [
        {"kind": "raw", "sha256": sha, "size_bytes": size}
        for sha, size in sorted(raw_objects.items())
    ] + [{"kind": "parsed", "sha256": parsed_sha, "size_bytes": parsed_size}]


def missing_raw_objects(conn, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    missing = []
    seen: set[str] = set()
    for row in manifest.get("raw_files") or []:
        sha = str(row.get("sha256") or "").lower()
        if sha in seen:
            continue
        seen.add(sha)
        raw_size = row.get("size_bytes")
        size = int(raw_size) if raw_size is not None else None
        if not _SHA256_RE.fullmatch(sha) or (size is not None and size < 0):
            raise ValueError("Manifest contains an invalid raw object")
        if size is None:
            object_row = conn.execute(
                db.text("SELECT size_bytes FROM raw_objects WHERE sha256=:sha"),
                {"sha": sha},
            ).first()
            exists = bool(
                object_row
                and os.path.exists(raw_object_abs_path(sha))
                and os.path.getsize(raw_object_abs_path(sha))
                == int(object_row._mapping["size_bytes"])
            )
        else:
            exists = _raw_object_exists(conn, sha, size)
        if not exists:
            missing.append({"sha256": sha, "size_bytes": size})
    return missing


def create_session(
    conn,
    manifest: dict[str, Any],
    preflight: dict[str, Any],
    *,
    imported_by: int | None,
    operation_id: str | None,
) -> dict[str, Any]:
    cleanup_expired(conn)
    now = db.utcnow()
    source_node_id = str(manifest["source_node_id"])
    resume_key = manifest_resume_key(manifest)
    manifest_hash = manifest_sha256(manifest)
    existing = conn.execute(
        db.text(
            """SELECT * FROM sync_upload_sessions
               WHERE source_node_id=:source_node_id AND resume_key=:resume_key
               FOR UPDATE"""
        ),
        {"source_node_id": source_node_id, "resume_key": resume_key},
    ).first()
    if existing:
        session = dict(existing._mapping)
        if session["status"] == "completed":
            conn.execute(
                db.text(
                    """UPDATE sync_upload_sessions
                       SET operation_id=:operation_id, updated_at=:updated_at,
                           expires_at=:expires_at
                       WHERE session_id=:session_id"""
                ),
                {
                    "session_id": str(session["session_id"]),
                    "operation_id": operation_id,
                    "updated_at": now,
                    "expires_at": now + SESSION_TTL,
                },
            )
            return describe_session(conn, str(session["session_id"]))
        session_id = str(session["session_id"])
        next_status = "importing" if session["status"] == "importing" else "uploading"
        conn.execute(
            db.text(
                """UPDATE sync_upload_sessions
                   SET package_id=:package_id, operation_id=:operation_id,
                       manifest_sha256=:manifest_sha256, manifest_json=:manifest_json,
                       preflight_json=:preflight_json, status=:status,
                       imported_by=:imported_by, updated_at=:updated_at,
                       expires_at=:expires_at, error_json=NULL
                   WHERE session_id=:session_id"""
            ),
            {
                "session_id": session_id,
                "package_id": str(manifest["package_id"]),
                "operation_id": operation_id,
                "manifest_sha256": manifest_hash,
                "manifest_json": json.dumps(manifest, ensure_ascii=False, default=str),
                "preflight_json": json.dumps(preflight, ensure_ascii=False, default=str),
                "status": next_status,
                "imported_by": imported_by,
                "updated_at": now,
                "expires_at": now + SESSION_TTL,
            },
        )
    else:
        session_id = uuid.uuid4().hex
        conn.execute(
            db.text(
                """INSERT INTO sync_upload_sessions
                     (session_id, resume_key, package_id, source_node_id, operation_id,
                      manifest_sha256, manifest_json, preflight_json, status,
                      imported_by, created_at, updated_at, expires_at)
                   VALUES
                     (:session_id, :resume_key, :package_id, :source_node_id, :operation_id,
                      :manifest_sha256, :manifest_json, :preflight_json, 'uploading',
                      :imported_by, :created_at, :updated_at, :expires_at)"""
            ),
            {
                "session_id": session_id,
                "resume_key": resume_key,
                "package_id": str(manifest["package_id"]),
                "source_node_id": source_node_id,
                "operation_id": operation_id,
                "manifest_sha256": manifest_hash,
                "manifest_json": json.dumps(manifest, ensure_ascii=False, default=str),
                "preflight_json": json.dumps(preflight, ensure_ascii=False, default=str),
                "imported_by": imported_by,
                "created_at": now,
                "updated_at": now,
                "expires_at": now + SESSION_TTL,
            },
        )

    for item in _manifest_objects(manifest):
        kind = item["kind"]
        sha = item["sha256"]
        size = int(item["size_bytes"])
        if kind == "raw" and _raw_object_exists(conn, sha, size):
            conn.execute(
                db.text(
                    """UPDATE sync_upload_objects
                       SET received_bytes=:size_bytes, status='complete',
                           completed_path=:completed_path, updated_at=:updated_at
                       WHERE session_id=:session_id AND object_kind='raw'
                         AND sha256=:sha"""
                ),
                {
                    "session_id": session_id,
                    "sha": sha,
                    "size_bytes": size,
                    "completed_path": raw_object_rel_path(sha),
                    "updated_at": now,
                },
            )
            conn.execute(
                db.text(
                    """DELETE FROM sync_upload_chunks
                       WHERE session_id=:session_id AND object_kind='raw'
                         AND object_sha256=:sha"""
                ),
                {"session_id": session_id, "sha": sha},
            )
            shutil.rmtree(
                os.path.dirname(_chunk_path(session_id, "raw", sha, 0)),
                ignore_errors=True,
            )
            continue
        total_chunks = max(1, math.ceil(size / CHUNK_SIZE))
        conn.execute(
            db.text(
                """INSERT INTO sync_upload_objects
                     (session_id, object_kind, sha256, size_bytes, chunk_size,
                      total_chunks, received_bytes, status, created_at, updated_at)
                   VALUES
                     (:session_id, :object_kind, :sha256, :size_bytes, :chunk_size,
                      :total_chunks, 0, 'pending', :created_at, :updated_at)
                   ON DUPLICATE KEY UPDATE updated_at=VALUES(updated_at)"""
            ),
            {
                "session_id": session_id,
                "object_kind": kind,
                "sha256": sha,
                "size_bytes": size,
                "chunk_size": CHUNK_SIZE,
                "total_chunks": total_chunks,
                "created_at": now,
                "updated_at": now,
            },
        )
    os.makedirs(_session_root(session_id), exist_ok=True)
    return describe_session(conn, session_id)


def describe_session(conn, session_id: str) -> dict[str, Any]:
    row = conn.execute(
        db.text("SELECT * FROM sync_upload_sessions WHERE session_id=:session_id"),
        {"session_id": session_id},
    ).first()
    if not row:
        raise KeyError("upload_session")
    session = dict(row._mapping)
    object_rows = conn.execute(
        db.text(
            """SELECT * FROM sync_upload_objects
               WHERE session_id=:session_id ORDER BY object_kind, sha256"""
        ),
        {"session_id": session_id},
    ).fetchall()
    objects = []
    for object_row in object_rows:
        item = dict(object_row._mapping)
        chunks = conn.execute(
            db.text(
                """SELECT chunk_index, offset_bytes, size_bytes, chunk_sha256
                   FROM sync_upload_chunks
                   WHERE session_id=:session_id AND object_kind=:object_kind
                     AND object_sha256=:sha256 ORDER BY chunk_index"""
            ),
            {
                "session_id": session_id,
                "object_kind": item["object_kind"],
                "sha256": item["sha256"],
            },
        ).fetchall()
        item["received_chunks"] = [dict(chunk._mapping) for chunk in chunks]
        objects.append(item)
    result = {
        "session_id": session_id,
        "resume_key": session["resume_key"],
        "package_id": session["package_id"],
        "source_node_id": session["source_node_id"],
        "operation_id": session.get("operation_id"),
        "manifest_sha256": session["manifest_sha256"],
        "status": session["status"],
        "preflight": _json_value(session["preflight_json"]),
        "objects": objects,
        "expires_at": session["expires_at"].isoformat(timespec="seconds"),
    }
    if session.get("result_json") is not None:
        result["result"] = _json_value(session["result_json"])
    if session.get("error_json") is not None:
        result["error"] = _json_value(session["error_json"])
    return result


def store_chunk(
    conn,
    session_id: str,
    kind: str,
    sha256: str,
    chunk_index: int,
    offset_bytes: int,
    chunk_sha256: str,
    payload: bytes,
) -> dict[str, Any]:
    sha = str(sha256 or "").lower()
    chunk_sha = str(chunk_sha256 or "").lower()
    if kind not in _KINDS or not _SHA256_RE.fullmatch(sha):
        raise ValueError("Invalid upload object identity")
    if not _SHA256_RE.fullmatch(chunk_sha):
        raise ValueError("Invalid chunk SHA256")
    row = conn.execute(
        db.text(
            """SELECT object.*, session.status AS session_status,
                      session.expires_at AS session_expires_at
               FROM sync_upload_objects object
               JOIN sync_upload_sessions session ON session.session_id=object.session_id
               WHERE object.session_id=:session_id AND object.object_kind=:kind
                 AND object.sha256=:sha
               FOR UPDATE"""
        ),
        {"session_id": session_id, "kind": kind, "sha": sha},
    ).first()
    if not row:
        raise KeyError("upload_object")
    item = dict(row._mapping)
    if item["session_expires_at"] < db.utcnow():
        raise ValueError("Upload session has expired")
    if item["session_status"] not in {"uploading", "failed"}:
        raise ValueError("Upload session is not accepting chunks")
    if item["status"] == "complete":
        return describe_session(conn, session_id)
    expected_offset = int(chunk_index) * int(item["chunk_size"])
    if chunk_index < 0 or chunk_index >= int(item["total_chunks"]):
        raise ValueError("Chunk index is out of range")
    if int(offset_bytes) != expected_offset:
        raise ValueError("Chunk offset does not match fixed chunk index")
    expected_size = min(
        int(item["chunk_size"]), int(item["size_bytes"]) - expected_offset
    )
    if len(payload) != expected_size:
        raise ValueError("Chunk size does not match expected range")
    if hashlib.sha256(payload).hexdigest() != chunk_sha:
        raise ValueError("Chunk SHA256 mismatch")

    existing = conn.execute(
        db.text(
            """SELECT size_bytes, chunk_sha256 FROM sync_upload_chunks
               WHERE session_id=:session_id AND object_kind=:kind
                 AND object_sha256=:sha AND chunk_index=:chunk_index"""
        ),
        {
            "session_id": session_id,
            "kind": kind,
            "sha": sha,
            "chunk_index": int(chunk_index),
        },
    ).first()
    if existing:
        if (
            int(existing._mapping["size_bytes"]) != len(payload)
            or str(existing._mapping["chunk_sha256"]) != chunk_sha
        ):
            raise ValueError("Chunk index was already uploaded with different content")
        return describe_session(conn, session_id)

    path = _chunk_path(session_id, kind, sha, int(chunk_index))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".chunk_", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    rel_path = os.path.relpath(path, db.SERVER_DATA_DIR).replace(os.sep, "/")
    now = db.utcnow()
    conn.execute(
        db.text(
            """INSERT INTO sync_upload_chunks
                 (session_id, object_kind, object_sha256, chunk_index,
                  offset_bytes, size_bytes, chunk_sha256, storage_rel_path, created_at)
               VALUES
                 (:session_id, :kind, :sha, :chunk_index,
                  :offset_bytes, :size_bytes, :chunk_sha256, :storage_rel_path, :created_at)"""
        ),
        {
            "session_id": session_id,
            "kind": kind,
            "sha": sha,
            "chunk_index": int(chunk_index),
            "offset_bytes": int(offset_bytes),
            "size_bytes": len(payload),
            "chunk_sha256": chunk_sha,
            "storage_rel_path": rel_path,
            "created_at": now,
        },
    )
    conn.execute(
        db.text(
            """UPDATE sync_upload_objects
               SET received_bytes=received_bytes+:size_bytes, status='uploading',
                   updated_at=:updated_at
               WHERE session_id=:session_id AND object_kind=:kind AND sha256=:sha"""
        ),
        {
            "session_id": session_id,
            "kind": kind,
            "sha": sha,
            "size_bytes": len(payload),
            "updated_at": now,
        },
    )
    conn.execute(
        db.text(
            """UPDATE sync_upload_sessions
               SET updated_at=:updated_at, expires_at=:expires_at
               WHERE session_id=:session_id"""
        ),
        {
            "session_id": session_id,
            "updated_at": now,
            "expires_at": now + SESSION_TTL,
        },
    )
    _complete_object_if_ready(conn, session_id, kind, sha)
    return describe_session(conn, session_id)


def _complete_object_if_ready(conn, session_id: str, kind: str, sha256: str) -> None:
    object_row = conn.execute(
        db.text(
            """SELECT * FROM sync_upload_objects
               WHERE session_id=:session_id AND object_kind=:kind AND sha256=:sha"""
        ),
        {"session_id": session_id, "kind": kind, "sha": sha256},
    ).first()
    item = dict(object_row._mapping)
    if int(item["received_bytes"]) != int(item["size_bytes"]):
        return
    chunks = conn.execute(
        db.text(
            """SELECT * FROM sync_upload_chunks
               WHERE session_id=:session_id AND object_kind=:kind
                 AND object_sha256=:sha ORDER BY chunk_index"""
        ),
        {"session_id": session_id, "kind": kind, "sha": sha256},
    ).fetchall()
    if len(chunks) != int(item["total_chunks"]):
        return
    root = _session_root(session_id)
    fd, assembled_path = tempfile.mkstemp(prefix=f".{kind}_", dir=root)
    digest = hashlib.sha256()
    written = 0
    try:
        with os.fdopen(fd, "wb") as output:
            for expected_index, chunk in enumerate(chunks):
                values = chunk._mapping
                if int(values["chunk_index"]) != expected_index:
                    raise ValueError("Upload chunks are not contiguous")
                path = os.path.abspath(os.path.join(db.SERVER_DATA_DIR, *str(values["storage_rel_path"]).split("/")))
                if os.path.commonpath([path, root]) != root:
                    raise ValueError("Upload chunk path escaped session storage")
                with open(path, "rb") as source:
                    while block := source.read(1024 * 1024):
                        output.write(block)
                        digest.update(block)
                        written += len(block)
            output.flush()
            os.fsync(output.fileno())
        if written != int(item["size_bytes"]) or digest.hexdigest() != sha256:
            raise ValueError("Completed upload object failed size or SHA256 validation")
        if kind == "raw":
            destination = raw_object_abs_path(sha256)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            if os.path.exists(destination):
                if os.path.getsize(destination) != written:
                    raise ValueError("Existing raw object size mismatch")
                os.remove(assembled_path)
            else:
                os.replace(assembled_path, destination)
            now = db.utcnow()
            conn.execute(
                db.text(
                    """INSERT INTO raw_objects
                         (sha256, size_bytes, storage_rel_path, created_at, verified_at)
                       VALUES (:sha, :size_bytes, :storage_rel_path, :created_at, :verified_at)
                       ON DUPLICATE KEY UPDATE verified_at=VALUES(verified_at)"""
                ),
                {
                    "sha": sha256,
                    "size_bytes": written,
                    "storage_rel_path": raw_object_rel_path(sha256),
                    "created_at": now,
                    "verified_at": now,
                },
            )
            completed_path = raw_object_rel_path(sha256)
        else:
            destination = os.path.join(root, f"parsed_{sha256}.sqlite")
            os.replace(assembled_path, destination)
            completed_path = os.path.relpath(destination, db.SERVER_DATA_DIR).replace(os.sep, "/")
        conn.execute(
            db.text(
                """UPDATE sync_upload_objects
                   SET status='complete', completed_path=:completed_path,
                       updated_at=:updated_at
                   WHERE session_id=:session_id AND object_kind=:kind AND sha256=:sha"""
            ),
            {
                "session_id": session_id,
                "kind": kind,
                "sha": sha256,
                "completed_path": completed_path,
                "updated_at": db.utcnow(),
            },
        )
        conn.execute(
            db.text(
                """DELETE FROM sync_upload_chunks
                   WHERE session_id=:session_id AND object_kind=:kind
                     AND object_sha256=:sha"""
            ),
            {"session_id": session_id, "kind": kind, "sha": sha256},
        )
        shutil.rmtree(os.path.dirname(_chunk_path(session_id, kind, sha256, 0)), ignore_errors=True)
    finally:
        if os.path.exists(assembled_path):
            os.remove(assembled_path)


def session_import_inputs(conn, session_id: str) -> tuple[dict[str, Any], str, dict[str, Any]]:
    row = conn.execute(
        db.text(
            """SELECT * FROM sync_upload_sessions
               WHERE session_id=:session_id FOR UPDATE"""
        ),
        {"session_id": session_id},
    ).first()
    if not row:
        raise KeyError("upload_session")
    session = dict(row._mapping)
    if session["status"] == "completed":
        return _json_value(session["manifest_json"]), "", session
    if session["expires_at"] < db.utcnow():
        raise ValueError("Upload session has expired")
    pending = conn.execute(
        db.text(
            """SELECT COUNT(*) AS count FROM sync_upload_objects
               WHERE session_id=:session_id AND status!='complete'"""
        ),
        {"session_id": session_id},
    ).first()
    if int(pending._mapping["count"] or 0):
        raise ValueError("Upload session still has incomplete objects")
    parsed = conn.execute(
        db.text(
            """SELECT completed_path FROM sync_upload_objects
               WHERE session_id=:session_id AND object_kind='parsed'"""
        ),
        {"session_id": session_id},
    ).first()
    if not parsed or not parsed._mapping["completed_path"]:
        raise ValueError("Upload session has no completed parsed object")
    parsed_path = os.path.abspath(
        os.path.join(db.SERVER_DATA_DIR, *str(parsed._mapping["completed_path"]).split("/"))
    )
    root = _session_root(session_id)
    if os.path.commonpath([parsed_path, root]) != root or not os.path.exists(parsed_path):
        raise ValueError("Upload session parsed object is missing")
    return _json_value(session["manifest_json"]), parsed_path, session


def mark_importing(conn, session_id: str) -> None:
    conn.execute(
        db.text(
            """UPDATE sync_upload_sessions
               SET status='importing', updated_at=:updated_at
               WHERE session_id=:session_id"""
        ),
        {"session_id": session_id, "updated_at": db.utcnow()},
    )


def mark_completed(conn, session_id: str, result: dict[str, Any]) -> None:
    conn.execute(
        db.text(
            """UPDATE sync_upload_sessions
               SET status='completed', result_json=:result_json,
                   updated_at=:updated_at, expires_at=:expires_at
               WHERE session_id=:session_id"""
        ),
        {
            "session_id": session_id,
            "result_json": json.dumps(result, ensure_ascii=False, default=str),
            "updated_at": db.utcnow(),
            "expires_at": db.utcnow() + SESSION_TTL,
        },
    )


def mark_failed(conn, session_id: str, error: Any) -> None:
    conn.execute(
        db.text(
            """UPDATE sync_upload_sessions
               SET status='failed', error_json=:error_json, updated_at=:updated_at
               WHERE session_id=:session_id"""
        ),
        {
            "session_id": session_id,
            "error_json": json.dumps(error, ensure_ascii=False, default=str),
            "updated_at": db.utcnow(),
        },
    )


def ensure_raw_object_from_zip(
    conn,
    bundle_path: str,
    package_path: str,
    sha256: str,
    size_bytes: int,
) -> str:
    if _raw_object_exists(conn, sha256, size_bytes):
        return raw_object_rel_path(sha256)
    destination = raw_object_abs_path(sha256)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".raw_", dir=os.path.dirname(destination))
    digest = hashlib.sha256()
    written = 0
    try:
        with zipfile.ZipFile(bundle_path, "r") as archive:
            with archive.open(package_path) as source, os.fdopen(fd, "wb") as output:
                while block := source.read(1024 * 1024):
                    output.write(block)
                    digest.update(block)
                    written += len(block)
                output.flush()
                os.fsync(output.fileno())
        if written != size_bytes or digest.hexdigest() != sha256:
            raise ValueError("Raw file size or SHA256 does not match manifest")
        if os.path.exists(destination):
            os.remove(temp_path)
        else:
            os.replace(temp_path, destination)
        now = db.utcnow()
        conn.execute(
            db.text(
                """INSERT INTO raw_objects
                     (sha256, size_bytes, storage_rel_path, created_at, verified_at)
                   VALUES (:sha, :size_bytes, :storage_rel_path, :created_at, :verified_at)
                   ON DUPLICATE KEY UPDATE verified_at=VALUES(verified_at)"""
            ),
            {
                "sha": sha256,
                "size_bytes": size_bytes,
                "storage_rel_path": raw_object_rel_path(sha256),
                "created_at": now,
                "verified_at": now,
            },
        )
        return raw_object_rel_path(sha256)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
