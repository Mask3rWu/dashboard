"""Server user administration routes."""

from fastapi import APIRouter, Depends, HTTPException

from backend import auth as auth_helpers
from backend import server_database as db
from backend.api.server.dependencies import connection, public_user_payload, require_user
from backend.api.server.schemas import CreateUserRequest, UpdateUserRequest


router = APIRouter()


@router.post("/api/users")
def create_user(req: CreateUserRequest, user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "manage_users")
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "用户名不能为空")
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if req.role not in ("admin", "user"):
        raise HTTPException(400, "角色必须是 admin 或 user")
    try:
        user_id = db.create_user(conn, username, auth_helpers.hash_password(req.password), req.role)
    except Exception as exc:
        if db.is_integrity_error(exc):
            raise HTTPException(400, f"用户 '{username}' 已存在") from exc
        raise
    return public_user_payload(db.get_user_by_id(conn, user_id))


@router.get("/api/users")
def list_users(user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "manage_users")
    return {"users": [public_user_payload(item) for item in db.list_users(conn)]}


@router.patch("/api/users/{user_id}")
def update_user(user_id: int, req: UpdateUserRequest, user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "manage_users")
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "用户名不能为空")
    target = db.get_user_by_id(conn, user_id)
    if not target or target.get("disabled_at") is not None:
        raise HTTPException(404, "用户不存在")
    try:
        db.update_username(conn, user_id, username)
    except Exception as exc:
        if db.is_integrity_error(exc):
            raise HTTPException(400, f"用户 '{username}' 已存在") from exc
        raise
    return public_user_payload(db.get_user_by_id(conn, user_id))


@router.post("/api/users/{user_id}/reset-password")
def reset_user_password(user_id: int, user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "manage_users")
    target = db.get_user_by_id(conn, user_id)
    if not target or target.get("disabled_at") is not None:
        raise HTTPException(404, "用户不存在")
    db.update_user_password(conn, user_id, auth_helpers.hash_password("123456"))
    return public_user_payload(db.get_user_by_id(conn, user_id))


@router.delete("/api/users/{user_id}")
def delete_user(user_id: int, user=Depends(require_user), conn=Depends(connection)):
    db.require_capability(user, "manage_users")
    if int(user["id"]) == user_id:
        raise HTTPException(400, "不能删除当前登录用户")
    target = db.get_user_by_id(conn, user_id)
    if not target or target.get("disabled_at") is not None:
        raise HTTPException(404, "用户不存在")
    if target.get("role") == "admin" and db.active_admin_count(conn) <= 1:
        raise HTTPException(400, "不能删除最后一个管理员")
    db.disable_user(conn, user_id)
    return {"ok": True}
