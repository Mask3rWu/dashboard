"""Desktop health and runtime context routes."""

import os

from fastapi import APIRouter, HTTPException, Request

from backend import runtime_context
from backend.api.desktop.dependencies import model_dump, server_token
from backend.api.desktop.schemas import RuntimeConfigUpdate, ServerLoginRequest
from backend.database import DATA_DIR, DB_BACKEND, DB_PATH
from backend.database import get_db
from backend.sync import client as sync_client


bootstrap_router = APIRouter()
router = APIRouter()


@bootstrap_router.get("/api/health")
def health_check(request: Request):
    """Health check used by the frontend before loading data."""
    frontend_dir = request.app.state.frontend_dir
    return {
        "status": "ok",
        "version": request.app.version,
        "data_dir": DATA_DIR,
        "db_backend": DB_BACKEND,
        "db_path": DB_PATH,
        "db_exists": os.path.exists(DB_PATH),
        "frontend_dir_exists": os.path.isdir(frontend_dir),
    }


@router.get("/api/runtime/context")
def get_runtime_context_api(request: Request):
    conn = get_db()
    try:
        payload = runtime_context.runtime_context(conn, server_token(None, request))
        conn.commit()
        return payload
    finally:
        conn.close()


@router.patch("/api/runtime/config")
def patch_runtime_config(req: RuntimeConfigUpdate, request: Request):
    conn = get_db()
    try:
        runtime_context.update_runtime_config(conn, model_dump(req, exclude_unset=True))
        payload = runtime_context.runtime_context(conn, server_token(None, request))
        conn.commit()
        return payload
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@router.post("/api/server-auth/login")
def server_login(req: ServerLoginRequest):
    conn = get_db()
    try:
        server_base_url = sync_client.normalize_base_url(
            runtime_context.get_server_base_url(conn)
        )
        return sync_client.login(server_base_url, req.username.strip(), req.password)
    except sync_client.SyncClientError as exc:
        raise HTTPException(
            exc.status_code or 502, exc.to_error_json("server_login")
        ) from exc
    finally:
        conn.close()


@router.post("/api/server-auth/logout")
def server_logout(request: Request):
    token = server_token(None, request)
    conn = get_db()
    try:
        server_base_url = runtime_context.get_server_base_url(conn)
        if server_base_url and token:
            try:
                sync_client.logout(server_base_url, token=token)
            except sync_client.SyncClientError:
                pass
        return {"ok": True}
    finally:
        conn.close()
