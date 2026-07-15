"""Desktop application context and authentication routes."""

from fastapi import APIRouter, HTTPException, Request

from backend import auth as auth_helpers
from backend import runtime_context
from backend.api.desktop.dependencies import server_header_token
from backend.api.desktop.schemas import (
    AppContextUpdate,
    ChangePasswordRequest,
    LoginRequest,
)
from backend.database import get_db
from backend.permissions import (
    get_app_context,
    get_capabilities,
    get_current_user,
    require_capability,
    set_app_context,
)
from backend.repositories import users as user_repository
from backend.sync import client as sync_client


router = APIRouter()


def _public_user(user):
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "created_at": user.get("created_at"),
        "password_changed_at": user.get("password_changed_at"),
    }


def _context_payload(conn, request: Request, user=None):
    context = get_app_context(conn, request)
    if user is None:
        user = get_current_user(conn, request)
    return {
        **context,
        "user": _public_user(user),
        "capabilities": get_capabilities(context, user),
    }


@router.get("/api/app/context")
def get_app_context_api(request: Request):
    conn = get_db()
    try:
        payload = _context_payload(conn, request)
        conn.commit()
        return payload
    finally:
        conn.close()


@router.patch("/api/app/context")
def patch_app_context(req: AppContextUpdate, request: Request):
    conn = get_db()
    try:
        set_app_context(conn, req.model_dump(exclude_none=True))
        payload = _context_payload(conn, request)
        conn.commit()
        return payload
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()


@router.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    conn = get_db()
    try:
        get_app_context(conn, request)
        username = req.username.strip()
        server_base_url = runtime_context.get_server_base_url(conn)
        online_error = None
        if server_base_url:
            try:
                server_auth = sync_client.login(
                    server_base_url, username, req.password, timeout=5
                )
                server_user = (
                    server_auth.get("user")
                    if isinstance(server_auth.get("user"), dict)
                    else None
                )
                if not server_user:
                    raise HTTPException(502, "中心服务器未返回用户信息")
                role = str(server_user.get("role") or "user")
                if role not in ("admin", "user"):
                    role = "user"
                user_id = user_repository.upsert_user_cache(
                    conn,
                    username,
                    auth_helpers.hash_password(req.password),
                    role,
                    created_at=server_user.get("created_at"),
                    password_changed_at=server_user.get("password_changed_at"),
                )
                token = auth_helpers.create_session(conn, user_id)
                user = user_repository.get_user_by_username(conn, username)
                conn.commit()
                return {
                    **_context_payload(conn, request, user=dict(user)),
                    "token": token,
                    "server_token": server_auth.get("token"),
                    "login_mode": "online",
                }
            except sync_client.SyncClientError as exc:
                if exc.status_code in (400, 401, 403):
                    raise HTTPException(exc.status_code, str(exc)) from exc
                online_error = str(exc)

        row = user_repository.get_user_by_username(conn, username)
        if not row or not auth_helpers.verify_password(req.password, row["password_hash"]):
            detail = "用户名或密码不正确"
            if online_error:
                detail = f"无法连接中心服务器，且本地未找到可用预置账号或密码不正确：{online_error}"
            raise HTTPException(401, detail)
        token = auth_helpers.create_session(conn, row["id"])
        user = {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "created_at": row["created_at"],
            "password_changed_at": row["password_changed_at"],
        }
        conn.commit()
        return {
            **_context_payload(conn, request, user=user),
            "token": token,
            "login_mode": "offline",
        }
    finally:
        conn.close()


@router.post("/api/auth/logout")
def logout(request: Request):
    token = auth_helpers.extract_bearer_token(request)
    conn = get_db()
    try:
        if token:
            auth_helpers.delete_session(conn, token)
            conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.get("/api/auth/me")
def auth_me(request: Request):
    conn = get_db()
    try:
        payload = _context_payload(conn, request)
        conn.commit()
        return payload
    finally:
        conn.close()


@router.post("/api/auth/change-password")
def change_password(req: ChangePasswordRequest, request: Request):
    if len(req.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    conn = get_db()
    try:
        user = require_capability(conn, request, "change_own_password")
        server_token = server_header_token(request)
        server_base_url = runtime_context.get_server_base_url(conn)
        if server_token and server_base_url:
            try:
                sync_client.change_password(
                    server_base_url,
                    req.old_password,
                    req.new_password,
                    token=server_token,
                    timeout=5,
                )
            except sync_client.SyncClientError as exc:
                raise HTTPException(exc.status_code or 502, str(exc)) from exc
        auth_helpers.change_password(conn, user["id"], req.old_password, req.new_password)
        conn.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()
