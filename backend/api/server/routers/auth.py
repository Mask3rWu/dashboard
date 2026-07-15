"""Server health, authentication, and capability routes."""

from fastapi import APIRouter, Depends, HTTPException, Request

from backend import auth as auth_helpers
from backend import server_database as db
from backend.api.server.dependencies import (
    auth_payload,
    connection,
    current_user,
    require_user,
)
from backend.api.server.schemas import ChangePasswordRequest, LoginRequest


router = APIRouter()
capabilities_router = APIRouter()


@router.get("/api/health")
def health(request: Request, conn=Depends(connection)):
    conn.exec_driver_sql("SELECT 1")
    return {
        "status": "ok", "version": request.app.version, "db": "mysql",
        "server_data_dir": db.SERVER_DATA_DIR,
        "capabilities": sorted(db.SERVER_CAPABILITIES),
    }


@router.post("/api/auth/login")
def login(req: LoginRequest, conn=Depends(connection)):
    username = req.username.strip()
    user = db.get_user_by_username(conn, username)
    if not user or user.get("disabled_at") is not None or not auth_helpers.verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码不正确")
    token = db.create_session(conn, int(user["id"]))
    return auth_payload(user, token)


@router.post("/api/auth/logout")
def logout(request: Request, conn=Depends(connection)):
    token = auth_helpers.extract_bearer_token(request)
    if token:
        db.revoke_session(conn, token)
    return {"ok": True}


@router.get("/api/auth/me")
def auth_me(user=Depends(current_user)):
    return auth_payload(user)


@router.post("/api/auth/change-password")
def change_password(req: ChangePasswordRequest, user=Depends(require_user), conn=Depends(connection)):
    if len(req.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    stored = db.get_user_by_username(conn, user["username"])
    if not stored or not auth_helpers.verify_password(req.old_password, stored["password_hash"]):
        raise HTTPException(400, "旧密码不正确")
    db.update_user_password(conn, int(user["id"]), auth_helpers.hash_password(req.new_password))
    return {"ok": True}


@capabilities_router.get("/api/capabilities")
def capabilities(user=Depends(current_user)):
    return {"capabilities": db.capabilities_for_user(user)}
