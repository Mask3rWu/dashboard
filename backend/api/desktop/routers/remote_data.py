"""Desktop proxy routes for browsing and caching collaboration-server data."""

from fastapi import APIRouter, HTTPException, Request

from backend import runtime_context
from backend.api.desktop.dependencies import model_dump, server_token
from backend.api.desktop.schemas import (
    RemoteFlightDownloadRequest,
    RemoteFlightSearchRequest,
)
from backend.database import get_db
from backend.remote_model_sync import (
    ModelSyncConflict,
    annotate_remote_models,
    sync_model_definition,
)
from backend.sync import client as sync_client
from backend.sync import workflow


router = APIRouter()


def _server_context(request: Request) -> tuple[str, str | None]:
    conn = get_db()
    try:
        base_url = sync_client.normalize_base_url(
            runtime_context.get_server_base_url(conn)
        )
    finally:
        conn.close()
    return base_url, server_token(None, request)


def _proxy_error(exc: sync_client.SyncClientError) -> HTTPException:
    status = exc.status_code if exc.status_code and 400 <= exc.status_code < 500 else 502
    return HTTPException(status, exc.to_error_json("remote_data"))


@router.get("/api/remote-data/models")
def models(request: Request):
    base_url, token = _server_context(request)
    try:
        payload = sync_client.data_models(base_url, token=token)
        conn = get_db()
        try:
            return annotate_remote_models(conn, payload)
        finally:
            conn.close()
    except sync_client.SyncClientError as exc:
        raise _proxy_error(exc) from exc


@router.post("/api/remote-data/models/{model_id}/sync")
def sync_model(model_id: int, request: Request):
    base_url, token = _server_context(request)
    try:
        definition = sync_client.data_model_definition(base_url, model_id, token=token)
    except sync_client.SyncClientError as exc:
        raise _proxy_error(exc) from exc
    conn = get_db()
    try:
        return sync_model_definition(conn, definition)
    except ModelSyncConflict as exc:
        conn.rollback()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@router.get("/api/remote-data/models/{model_id}/aircraft")
def aircraft(model_id: int, request: Request):
    base_url, token = _server_context(request)
    try:
        return sync_client.data_aircraft(base_url, model_id, token=token)
    except sync_client.SyncClientError as exc:
        raise _proxy_error(exc) from exc


@router.get("/api/remote-data/models/{model_id}/columns")
def columns(model_id: int, request: Request):
    base_url, token = _server_context(request)
    try:
        return sync_client.data_model_columns(base_url, model_id, token=token)
    except sync_client.SyncClientError as exc:
        raise _proxy_error(exc) from exc


def _mark_local_cache(payload: dict) -> dict:
    flights = payload.get("flights") if isinstance(payload.get("flights"), list) else []
    server_ids = [int(item["id"]) for item in flights if item.get("id") is not None]
    local_by_server: dict[int, int] = {}
    if server_ids:
        conn = get_db()
        try:
            placeholders = ",".join("?" for _ in server_ids)
            rows = conn.execute(
                f"SELECT id, server_id FROM flights WHERE server_id IN ({placeholders})",
                server_ids,
            ).fetchall()
            local_by_server = {int(row["server_id"]): int(row["id"]) for row in rows}
        finally:
            conn.close()
    for item in flights:
        server_id = int(item["id"])
        item["downloaded"] = server_id in local_by_server
        item["local_id"] = local_by_server.get(server_id)
    return payload


@router.post("/api/remote-data/flights/search")
def search_flights(req: RemoteFlightSearchRequest, request: Request):
    base_url, token = _server_context(request)
    try:
        result = sync_client.search_data_flights(
            base_url, model_dump(req), token=token
        )
        return _mark_local_cache(result)
    except sync_client.SyncClientError as exc:
        raise _proxy_error(exc) from exc


@router.post("/api/remote-data/flights/download")
def download_flights(req: RemoteFlightDownloadRequest, request: Request):
    requested_ids = list(dict.fromkeys(int(value) for value in req.flight_ids))
    conn = get_db()
    try:
        local_model = conn.execute(
            """SELECT id FROM aircraft_models
               WHERE server_id=? AND deleted_at IS NULL AND server_deleted_at IS NULL""",
            (int(req.model_id),),
        ).fetchone()
        if not local_model:
            raise HTTPException(409, "该服务器机型尚未同步，请先点击机型列表中的“同步机型”")
        placeholders = ",".join("?" for _ in requested_ids)
        rows = conn.execute(
            f"""SELECT f.server_id
                FROM flights f
                JOIN aircraft a ON a.id=f.aircraft_id
                JOIN aircraft_models am ON am.id=a.model_id
                WHERE f.server_id IN ({placeholders}) AND am.server_id=?""",
            [*requested_ids, int(req.model_id)],
        ).fetchall()
        already_downloaded = {
            int(row["server_id"])
            for row in rows
            if row["server_id"] is not None
        }
    finally:
        conn.close()

    pending_ids = [flight_id for flight_id in requested_ids if flight_id not in already_downloaded]
    if not pending_ids:
        return {
            "ok": True,
            "status": "success",
            "report": {
                "created": {"models": 0, "aircraft": 0, "flights": 0},
                "updated": {"models": 0, "aircraft": 0, "flights": 0},
                "already_downloaded": {"flights": len(already_downloaded)},
                "conflicts": [],
                "warnings": [],
            },
        }
    try:
        result = workflow.pull(
            since="",
            token=server_token(None, request),
            operation_id=req.operation_id,
            flight_ids=pending_ids,
            model_id=req.model_id,
        )
        report = result.setdefault("report", {})
        report["already_downloaded"] = {"flights": len(already_downloaded)}
        return result
    except workflow.WorkflowError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
