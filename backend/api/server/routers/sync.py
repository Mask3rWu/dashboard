"""Server synchronization routes."""

import json
import os
import shutil
import tempfile
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from backend import server_database as db
from backend.api.server.dependencies import connection, require_user
from backend.sync import server as server_sync
from backend.sync import server_operations, upload_sessions
from backend.api.server.schemas import CreateUploadSessionRequest, MergeEntitiesRequest


router = APIRouter()


def _persist_upload_session_failure(session_id: str, error: Any) -> None:
    with db.get_engine().begin() as failure_conn:
        upload_sessions.mark_failed(failure_conn, session_id, error)


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


@router.post("/api/sync/sessions")
def create_sync_session(
    req: CreateUploadSessionRequest,
    user=Depends(require_user),
    conn=Depends(connection),
):
    db.require_capability(user, "sync_push")
    operation_id = req.operation_id or uuid.uuid4().hex
    server_operations.start(operation_id, "push", "Preparing resumable upload session")
    try:
        manifest = server_sync.validate_manifest(req.manifest)
        preflight = server_sync.build_preflight_plan(conn, manifest)
        if preflight.get("conflicts"):
            server_operations.finish(
                operation_id,
                status="failed",
                phase="server_preflight",
                message="Upload preflight found conflicts",
            )
            return {
                "ok": False,
                "status": "conflict",
                "operation_id": operation_id,
                "preflight": preflight,
                "conflicts": preflight.get("conflicts") or [],
            }
        result = upload_sessions.create_session(
            conn,
            manifest,
            preflight,
            imported_by=int(user["id"]),
            operation_id=operation_id,
        )
        result.update({"ok": True, "operation_id": operation_id})
        return result
    except ValueError as exc:
        server_operations.finish(
            operation_id,
            status="failed",
            phase="server_session_prepare",
            message=str(exc),
        )
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/sync/sessions/{session_id}")
def get_sync_session(session_id: str, user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "sync_push")
    try:
        return {"ok": True, **upload_sessions.describe_session(conn, session_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, "Upload session not found") from exc


@router.put(
    "/api/sync/sessions/{session_id}/objects/{object_kind}/{object_sha256}/chunks/{chunk_index}"
)
async def upload_sync_chunk(
    session_id: str,
    object_kind: str,
    object_sha256: str,
    chunk_index: int,
    request: Request,
    x_chunk_offset: int = Header(alias="X-Chunk-Offset"),
    x_chunk_sha256: str = Header(alias="X-Chunk-SHA256"),
    content_length: int | None = Header(default=None, alias="Content-Length"),
    user=Depends(require_user),
    conn=Depends(connection),
):
    db.require_capability(user, "sync_push")
    if content_length is not None and content_length > upload_sessions.CHUNK_SIZE:
        raise HTTPException(413, "Upload chunk is too large")
    payload = await request.body()
    if len(payload) > upload_sessions.CHUNK_SIZE:
        raise HTTPException(413, "Upload chunk is too large")
    try:
        result = upload_sessions.store_chunk(
            conn,
            session_id,
            object_kind,
            object_sha256.lower(),
            chunk_index,
            x_chunk_offset,
            x_chunk_sha256.lower(),
            payload,
        )
        object_rows = result.get("objects") or []
        server_operations.update(
            result.get("operation_id"),
            phase="server_receive_objects",
            message="Receiving content-addressed upload objects",
            current=sum(int(item.get("received_bytes") or 0) for item in object_rows),
            total=sum(int(item.get("size_bytes") or 0) for item in object_rows),
            unit="bytes",
        )
        return {"ok": True, **result}
    except KeyError as exc:
        raise HTTPException(404, "Upload object not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/sync/sessions/{session_id}/commit")
def commit_sync_session(
    session_id: str,
    user=Depends(require_user),
    conn=Depends(connection),
):
    db.require_capability(user, "sync_push")
    operation_id = None
    try:
        state = upload_sessions.describe_session(conn, session_id)
        operation_id = state.get("operation_id")
        server_operations.update(
            operation_id,
            phase="server_session_import",
            message="Importing completed upload session",
            force=True,
        )
        result = server_sync.import_push_session(
            conn,
            session_id,
            imported_by=int(user["id"]),
            operation_id=operation_id,
        )
        conn.commit()
        server_operations.finish(
            operation_id,
            status="completed" if result.get("ok") else "failed",
            phase="server_commit",
            message="Upload session import committed",
            metrics=result.get("metrics"),
        )
        return result
    except KeyError as exc:
        raise HTTPException(404, "Upload session not found") from exc
    except ValueError as exc:
        conn.rollback()
        _persist_upload_session_failure(session_id, {"message": str(exc)})
        server_operations.finish(
            operation_id,
            status="failed",
            phase="server_failed",
            message=str(exc),
        )
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        conn.rollback()
        _persist_upload_session_failure(session_id, {"message": str(exc)})
        server_operations.finish(
            operation_id,
            status="failed",
            phase="server_failed",
            message=str(exc),
        )
        raise


@router.post("/api/sync/push")
def sync_push(
    request: Request,
    response: Response,
    bundle: UploadFile = File(...),
    user=Depends(require_user),
    conn=Depends(connection),
):
    db.require_capability(user, "sync_push")
    operation_id = request.headers.get("X-Sync-Operation-Id") or uuid.uuid4().hex
    response.headers["X-Sync-Operation-Id"] = operation_id
    server_operations.start(operation_id, "push", "Receiving upload")
    suffix = os.path.splitext(bundle.filename or "")[1] or ".fapkg"
    fd, tmp_path = tempfile.mkstemp(prefix="flightanalyzer_push_", suffix=suffix)
    os.close(fd)
    try:
        receive_started = time.perf_counter()
        with open(tmp_path, "wb") as file:
            copied = 0
            started = time.perf_counter()
            while chunk := bundle.file.read(1024 * 1024):
                file.write(chunk)
                copied += len(chunk)
                elapsed = max(time.perf_counter() - started, 0.000001)
                server_operations.update(
                    operation_id,
                    phase="server_receive",
                    message="Writing upload to server temporary storage",
                    current=copied,
                    unit="bytes",
                    rate=round(copied / elapsed, 2),
                )
        receive_duration = time.perf_counter() - receive_started
        try:
            result = server_sync.import_push_bundle(
                conn,
                tmp_path,
                imported_by=int(user["id"]),
                operation_id=operation_id,
            )
            commit_started = time.perf_counter()
            conn.commit()
            commit_duration = time.perf_counter() - commit_started
            metrics = result.setdefault("metrics", {})
            phases = metrics.setdefault("phases", [])
            phases.insert(
                0,
                {
                    "phase": "server_receive",
                    "status": "completed",
                    "bytes": copied,
                    "duration_seconds": round(receive_duration, 6),
                    "bytes_per_second": round(copied / max(receive_duration, 0.000001), 2),
                },
            )
            phases.append(
                {
                    "phase": "server_commit",
                    "status": "completed",
                    "duration_seconds": round(commit_duration, 6),
                }
            )
            server_operations.finish(
                operation_id,
                status="completed" if result.get("ok") else "failed",
                phase="server_commit",
                message="Server import committed" if result.get("ok") else "Server import failed",
                metrics=metrics,
            )
            return result
        except ValueError as exc:
            server_operations.finish(
                operation_id,
                status="failed",
                phase="server_failed",
                message=str(exc),
            )
            raise HTTPException(400, str(exc)) from exc
        except json.JSONDecodeError as exc:
            server_operations.finish(
                operation_id,
                status="failed",
                phase="server_failed",
                message=f"Invalid manifest JSON: {exc}",
            )
            raise HTTPException(400, f"Invalid manifest JSON: {exc}") from exc
        except Exception as exc:
            server_operations.finish(
                operation_id,
                status="failed",
                phase="server_failed",
                message=str(exc),
            )
            raise
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
def sync_bundle(
    request: Request,
    since: str | None = Query(default=None),
    exclude_source_node_id: str | None = Query(default=None),
    flight_ids: list[int] | None = Query(default=None),
    model_id: int | None = Query(default=None),
    user=Depends(require_user),
    conn=Depends(connection),
):
    db.require_capability(user, "sync_pull")
    operation_id = request.headers.get("X-Sync-Operation-Id") or uuid.uuid4().hex
    server_operations.start(operation_id, "pull", "Generating pull bundle")
    try:
        result = server_sync.build_pull_bundle(
            conn,
            since,
            exclude_source_node_id=exclude_source_node_id,
            operation_id=operation_id,
            flight_ids=flight_ids,
            model_id=model_id,
        )
        server_operations.finish(
            operation_id,
            status="completed",
            phase="server_pull_ready",
            message="Pull bundle is ready",
            metrics=result.get("metrics"),
        )
    except ValueError as exc:
        server_operations.finish(
            operation_id,
            status="failed",
            phase="server_failed",
            message=str(exc),
        )
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        server_operations.finish(
            operation_id,
            status="failed",
            phase="server_failed",
            message=str(exc),
        )
        raise
    return FileResponse(
        result["path"], media_type="application/octet-stream",
        filename=os.path.basename(result["path"]),
        headers={
            "X-Sync-Cursor": str(result.get("current_cursor") or ""),
            "X-Sync-Package-Id": str(result.get("package_id") or ""),
            "X-Sync-Operation-Id": operation_id,
        },
    )


@router.get("/api/sync/operations/{operation_id}")
def sync_operation(operation_id: str, user=Depends(require_user), conn=Depends(connection)):
    item = server_operations.get(conn, operation_id)
    if not item:
        raise HTTPException(404, "Sync operation not found")
    return item


@router.post("/api/sync/merge/preflight")
def merge_preflight(req: MergeEntitiesRequest, user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "resolve_conflicts")
    try:
        return server_sync.preflight_entity_merge(
            conn, req.entity_type, req.source_id, req.target_id
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/sync/merge")
def merge_entities(req: MergeEntitiesRequest, user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "resolve_conflicts")
    try:
        return server_sync.execute_entity_merge(
            conn,
            req.entity_type,
            req.source_id,
            req.target_id,
            created_by=int(user["id"]),
        )
    except KeyError as exc:
        raise HTTPException(404, "Merge entity not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
