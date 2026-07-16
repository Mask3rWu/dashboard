"""Collaboration-server user management proxy routes."""

from fastapi import APIRouter, HTTPException, Request

from backend import runtime_context
from backend.api.desktop.dependencies import server_header_token
from backend.api.desktop.schemas import CreateUserRequest, UpdateUserRequest
from backend.database import get_db
from backend.sync import client as sync_client


router = APIRouter()


def _server_users_base_url(conn) -> str:
    server_base_url = runtime_context.get_server_base_url(conn)
    if not server_base_url:
        raise HTTPException(400, "未配置中心服务器地址")
    return sync_client.normalize_base_url(server_base_url)


def _server_users_token(request: Request) -> str:
    token = server_header_token(request)
    if not token:
        raise HTTPException(401, "请先使用中心服务器管理员账号登录")
    return token


def _public_server_user(user):
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "role": user.get("role"),
        "created_at": str(user.get("created_at")) if user.get("created_at") else None,
        "password_changed_at": (
            str(user.get("password_changed_at"))
            if user.get("password_changed_at")
            else None
        ),
        "disabled_at": str(user.get("disabled_at")) if user.get("disabled_at") else None,
    }


@router.get("/api/users")
def list_server_users(request: Request):
    conn = get_db()
    try:
        result = sync_client.list_users(
            _server_users_base_url(conn), token=_server_users_token(request), timeout=5
        )
        users = result.get("users") if isinstance(result.get("users"), list) else []
        return {"users": [_public_server_user(user) for user in users if isinstance(user, dict)]}
    except sync_client.SyncClientError as exc:
        raise HTTPException(exc.status_code or 502, str(exc)) from exc
    finally:
        conn.close()


@router.post("/api/users")
def create_server_user(req: CreateUserRequest, request: Request):
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "用户名不能为空")
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if req.role not in ("admin", "user"):
        raise HTTPException(400, "角色必须是 admin 或 user")
    conn = get_db()
    try:
        result = sync_client.create_user(
            _server_users_base_url(conn),
            username,
            req.password,
            req.role,
            token=_server_users_token(request),
            timeout=5,
        )
        return _public_server_user(result)
    except sync_client.SyncClientError as exc:
        raise HTTPException(exc.status_code or 502, str(exc)) from exc
    finally:
        conn.close()


@router.patch("/api/users/{user_id}")
def update_server_user(user_id: int, req: UpdateUserRequest, request: Request):
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "用户名不能为空")
    conn = get_db()
    try:
        result = sync_client.update_user(
            _server_users_base_url(conn),
            user_id,
            username,
            token=_server_users_token(request),
            timeout=5,
        )
        return _public_server_user(result)
    except sync_client.SyncClientError as exc:
        raise HTTPException(exc.status_code or 502, str(exc)) from exc
    finally:
        conn.close()


@router.post("/api/users/{user_id}/reset-password")
def reset_server_user_password(user_id: int, request: Request):
    conn = get_db()
    try:
        result = sync_client.reset_user_password(
            _server_users_base_url(conn),
            user_id,
            token=_server_users_token(request),
            timeout=5,
        )
        return _public_server_user(result)
    except sync_client.SyncClientError as exc:
        raise HTTPException(exc.status_code or 502, str(exc)) from exc
    finally:
        conn.close()


@router.delete("/api/users/{user_id}")
def delete_server_user(user_id: int, request: Request):
    conn = get_db()
    try:
        return sync_client.delete_user(
            _server_users_base_url(conn),
            user_id,
            token=_server_users_token(request),
            timeout=5,
        )
    except sync_client.SyncClientError as exc:
        raise HTTPException(exc.status_code or 502, str(exc)) from exc
    finally:
        conn.close()
