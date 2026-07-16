"""Server synchronization routes."""

import json
import os
import shutil
import tempfile
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from backend import server_database as db
from backend.api.server.dependencies import connection, require_user
from backend.sync import server as server_sync


router = APIRouter()


@router.post("/api/sync/preflight")
def sync_preflight(payload: dict[str, Any], user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "sync_push")
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else payload
    client_cursor = payload.get("client_cursor")
    if client_cursor is not None and not manifest.get("base_server_cursor"):
        manifest = {**manifest, "base_server_cursor": client_cursor}
    try:
        return server_sync.build_preflight_plan(conn, manifest)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/sync/push")
def sync_push(bundle: UploadFile = File(...), user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "sync_push")
    suffix = os.path.splitext(bundle.filename or "")[1] or ".fapkg"
    fd, tmp_path = tempfile.mkstemp(prefix="flightanalyzer_push_", suffix=suffix)
    os.close(fd)
    try:
        with open(tmp_path, "wb") as file:
            shutil.copyfileobj(bundle.file, file)
        try:
            result = server_sync.import_push_bundle(conn, tmp_path, imported_by=int(user["id"]))
            conn.commit()
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"Invalid manifest JSON: {exc}") from exc
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.get("/api/sync/changes")
def sync_changes(since: str | None = Query(default=None), user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "sync_pull")
    return server_sync.list_changes(conn, since)


@router.get("/api/sync/preview")
def sync_preview(since: str | None = Query(default=None), exclude_source_node_id: str | None = Query(default=None), user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "sync_pull")
    return server_sync.build_pull_preview(conn, since, exclude_source_node_id=exclude_source_node_id)


@router.get("/api/sync/bundle")
def sync_bundle(since: str | None = Query(default=None), exclude_source_node_id: str | None = Query(default=None), user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "sync_pull")
    result = server_sync.build_pull_bundle(conn, since, exclude_source_node_id=exclude_source_node_id)
    return FileResponse(
        result["path"], media_type="application/octet-stream",
        filename=os.path.basename(result["path"]),
        headers={
            "X-Sync-Cursor": str(result.get("current_cursor") or ""),
            "X-Sync-Package-Id": str(result.get("package_id") or ""),
        },
    )
