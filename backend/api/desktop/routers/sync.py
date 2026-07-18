"""Desktop synchronization HTTP routes."""

import json

from fastapi import APIRouter, HTTPException, Query, Request

from backend import runtime_context
from backend.api.desktop.dependencies import model_dump, server_token
from backend.api.desktop.schemas import (
    SyncAbandonRequest,
    SyncExportRequest,
    SyncImportPreviewRequest,
    SyncImportRequest,
    SyncPreviewRequest,
    SyncPullRequest,
    SyncPushBatchRequest,
    SyncRunRequest,
)
from backend.database import get_db
from backend.repositories import flights as flight_repository
from backend.sync import client as sync_client
from backend.sync import repository as sync_repository
from backend.sync import workflow
from backend.sync.local_import import get_import_report, import_package, preview_import
from backend.sync.package import export_package
from backend.sync.progress import get as get_progress


router = APIRouter()


def _json_or_none(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return {"raw": value}


@router.get("/api/sync/export-tree")
def get_sync_export_tree(q: str | None = None):
    keyword = (q or "").strip().lower()
    conn = get_db()
    try:
        models = {}
        for row in flight_repository.export_tree_rows(conn):
            item = dict(row)
            haystack = " ".join(
                str(item.get(key) or "")
                for key in (
                    "model_name",
                    "aircraft_name",
                    "flight_name",
                    "session_key",
                    "flight_date",
                    "record_location",
                    "record_weather",
                )
            ).lower()
            if keyword and keyword not in haystack:
                continue
            model = models.setdefault(
                item["model_id"],
                {"id": item["model_id"], "name": item["model_name"], "aircraft": {}},
            )
            aircraft = model["aircraft"].setdefault(
                item["aircraft_id"],
                {"id": item["aircraft_id"], "name": item["aircraft_name"], "flights": []},
            )
            aircraft["flights"].append(
                {
                    "id": item["flight_id"],
                    "name": item["flight_name"],
                    "session_key": item["session_key"],
                    "flight_date": item["flight_date"],
                    "start_time": item["start_time"],
                    "duration_sec": item["duration_sec"],
                    "record_location": item["record_location"],
                    "record_weather": item["record_weather"],
                }
            )
        tree = [
            {
                "id": model["id"],
                "name": model["name"],
                "aircraft": [
                    {"id": item["id"], "name": item["name"], "flights": item["flights"]}
                    for item in model["aircraft"].values()
                ],
            }
            for model in models.values()
        ]
        flight_count = sum(
            len(aircraft["flights"])
            for model in tree
            for aircraft in model["aircraft"]
        )
        return {"tree": tree, "flight_count": flight_count}
    finally:
        conn.close()


@router.get("/api/sync/queue")
def get_sync_queue():
    conn = get_db()
    try:
        items = []
        for row in sync_repository.list_upload_queue(conn):
            item = dict(row)
            item["sync_error"] = _json_or_none(item.get("sync_error_json"))
            items.append(item)
        base_queue = sync_repository.list_upload_base_queue(conn)
        base_items = []
        for row in [*base_queue["models"], *base_queue["aircraft"]]:
            item = dict(row)
            item["sync_error"] = _json_or_none(item.get("sync_error_json"))
            base_items.append(item)
        return {
            "summary": sync_repository.upload_queue_summary(conn),
            "items": items,
            "base_items": base_items,
        }
    finally:
        conn.close()


@router.get("/api/sync/progress/{operation_id}")
def get_sync_progress(operation_id: str):
    item = get_progress(operation_id)
    if not item:
        raise HTTPException(404, "Sync progress not found")
    return item


@router.post("/api/sync/preview")
def post_sync_preview(req: SyncPreviewRequest, request: Request):
    try:
        return workflow.preview(
            req.mode,
            flight_ids=req.flight_ids,
            since=req.since,
            token=server_token(req, request),
        )
    except workflow.WorkflowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post("/api/sync/export")
def post_sync_export(req: SyncExportRequest):
    conn = get_db()
    try:
        return export_package(conn, req.flight_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@router.post("/api/sync/push-batch")
def post_sync_push_batch(req: SyncPushBatchRequest):
    try:
        return workflow.build_push_batch(req.flight_ids)
    except workflow.WorkflowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post("/api/sync/push")
@router.post("/api/sync/retry")
def post_sync_push(req: SyncPushBatchRequest, request: Request):
    try:
        return workflow.push(
            req.flight_ids,
            token=server_token(req, request),
            operation_id=req.operation_id,
            progress_start=req.progress_start,
            progress_end=req.progress_end,
            progress_finalize=req.progress_finalize,
        )
    except workflow.WorkflowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post("/api/sync/run")
def post_sync_run(req: SyncRunRequest, request: Request):
    try:
        return workflow.run(
            req.flight_ids,
            since=req.since,
            token=server_token(req, request),
            operation_id=req.operation_id,
            pull_conflict_resolutions=req.pull_conflict_resolutions,
        )
    except workflow.WorkflowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.post("/api/sync/abandon")
def post_sync_abandon(req: SyncAbandonRequest):
    try:
        return workflow.abandon(req.flight_ids)
    except workflow.WorkflowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/api/sync/changes")
def get_sync_changes(
    request: Request,
    since: str | None = Query(default=None),
    server_token_query: str | None = Query(default=None, alias="server_token"),
):
    conn = get_db()
    try:
        server_base_url = sync_client.normalize_base_url(
            runtime_context.get_server_base_url(conn)
        )
        cursor = since
        if cursor is None:
            cursor = sync_repository.get_setting(conn, "last_pull_cursor", "")
        token = server_token_query or server_token(None, request)
        return sync_client.changes(server_base_url, cursor, token=token)
    except sync_client.SyncClientError as exc:
        raise HTTPException(502, exc.to_error_json("changes")) from exc
    finally:
        conn.close()


@router.post("/api/sync/pull")
def post_sync_pull(req: SyncPullRequest, request: Request):
    try:
        return workflow.pull(
            since=req.since,
            token=server_token(req, request),
            operation_id=req.operation_id,
            progress_start=req.progress_start,
            progress_end=req.progress_end,
            progress_finalize=req.progress_finalize,
            package_path=req.package_path,
            conflict_resolutions=req.conflict_resolutions,
            exclude_source_node_id=req.exclude_source_node_id,
        )
    except workflow.WorkflowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/api/sync/runs/{run_id}")
def get_sync_run(run_id: int):
    conn = get_db()
    try:
        row = sync_repository.get_sync_run(conn, run_id)
        if not row:
            raise HTTPException(404, "Sync run not found")
        item = dict(row)
        item["summary"] = _json_or_none(item.get("summary_json"))
        item["error"] = _json_or_none(item.get("error_json"))
        return item
    finally:
        conn.close()


@router.post("/api/sync/import/preview")
def post_sync_import_preview(req: SyncImportPreviewRequest):
    conn = get_db()
    try:
        return preview_import(conn, req.package_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@router.post("/api/sync/import")
def post_sync_import(req: SyncImportRequest):
    if req.conflict_policy not in (None, "skip", "update_records"):
        raise HTTPException(400, "Unsupported conflict_policy")
    metadata_strategy = req.metadata_strategy or (
        "package_wins" if req.conflict_policy == "update_records" else "target_wins"
    )
    if metadata_strategy not in ("package_wins", "target_wins"):
        raise HTTPException(400, "Unsupported metadata_strategy")
    conn = get_db()
    try:
        options = {
            "model_actions": [
                model_dump(item, exclude_unset=True) for item in req.model_actions
            ],
            "aircraft_mappings": [
                model_dump(item, exclude_unset=True) for item in req.aircraft_mappings
            ],
            "metadata_strategy": metadata_strategy,
        }
        return import_package(conn, req.package_path, options)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@router.get("/api/sync/imports/{import_id}")
def get_sync_import(import_id: int):
    conn = get_db()
    try:
        report = get_import_report(conn, import_id)
        if not report:
            raise HTTPException(404, "Import report not found")
        return report
    finally:
        conn.close()
