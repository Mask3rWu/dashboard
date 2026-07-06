"""Flight Analyzer collaboration server.

Run with:
    uvicorn server_app:app --host 127.0.0.1 --port 9000
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend import auth as auth_helpers
from backend import server_database as db


app = FastAPI(title="Flight Analyzer Server", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class ServerColumn(BaseModel):
    name: str
    label: str | None = None
    display_label: str | None = None
    unit: str = ""
    type: str | None = None
    data_type: str | None = None
    ordinal: int | None = None
    scale_factor: float = 1.0


class ServerDataType(BaseModel):
    display_label: str | None = None
    file_patterns: list[str] = Field(default_factory=list)
    is_alert: bool = False
    columns: list[ServerColumn] = Field(default_factory=list)


class CreateModelRequest(BaseModel):
    name: str
    client_uid: str | None = None
    source_node_id: str | None = None
    has_header: bool = True
    has_uav_send_id: bool = False
    extract_serial_from_path: bool = False
    data_types: dict[str, ServerDataType] = Field(default_factory=dict)


@app.on_event("startup")
def startup() -> None:
    db.init_server_schema()


def connection():
    with db.get_engine().begin() as conn:
        yield conn


def current_user(request: Request, conn=Depends(connection)):
    token = auth_helpers.extract_bearer_token(request)
    return db.get_user_by_session_token(conn, token)


def require_user(user=Depends(current_user)):
    if not user:
        raise HTTPException(401, "Authentication required")
    return user


def auth_payload(user: dict | None, token: str | None = None) -> dict:
    payload = {
        "user": db.public_user(user),
        "capabilities": db.capabilities_for_user(user),
    }
    if token is not None:
        payload["token"] = token
    return payload


@app.get("/api/health")
def health(conn=Depends(connection)):
    conn.exec_driver_sql("SELECT 1")
    return {
        "status": "ok",
        "version": app.version,
        "db": "mysql",
        "server_data_dir": db.SERVER_DATA_DIR,
        "capabilities": sorted(db.SERVER_CAPABILITIES),
    }


@app.post("/api/auth/login")
def login(req: LoginRequest, conn=Depends(connection)):
    username = req.username.strip()
    user = db.get_user_by_username(conn, username)
    if (
        not user
        or user.get("disabled_at") is not None
        or not auth_helpers.verify_password(req.password, user["password_hash"])
    ):
        raise HTTPException(401, "用户名或密码不正确")
    token = db.create_session(conn, int(user["id"]))
    return auth_payload(user, token)


@app.post("/api/auth/logout")
def logout(request: Request, conn=Depends(connection)):
    token = auth_helpers.extract_bearer_token(request)
    if token:
        db.revoke_session(conn, token)
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(user=Depends(current_user)):
    return auth_payload(user)


@app.post("/api/auth/change-password")
def change_password(
    req: ChangePasswordRequest,
    user=Depends(require_user),
    conn=Depends(connection),
):
    if len(req.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    stored = db.get_user_by_username(conn, user["username"])
    if not stored or not auth_helpers.verify_password(req.old_password, stored["password_hash"]):
        raise HTTPException(400, "旧密码不正确")
    db.update_user_password(conn, int(user["id"]), auth_helpers.hash_password(req.new_password))
    return {"ok": True}


@app.post("/api/users")
def create_user(
    req: CreateUserRequest,
    user=Depends(require_user),
    conn=Depends(connection),
):
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
            raise HTTPException(400, f"用户 '{username}' 已存在")
        raise
    return {"id": user_id, "username": username, "role": req.role}


@app.get("/api/capabilities")
def capabilities(user=Depends(current_user)):
    return {"capabilities": db.capabilities_for_user(user)}


@app.post("/api/models")
def create_model(
    req: CreateModelRequest,
    user=Depends(require_user),
    conn=Depends(connection),
):
    db.require_capability(user, "sync_push")
    try:
        payload = req.dict()
        created = db.create_model(conn, payload)
    except Exception as exc:
        if db.is_integrity_error(exc):
            raise HTTPException(400, f"机型已存在或配置重复: {db.integrity_error_detail(exc)}")
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return created
