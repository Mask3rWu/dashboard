"""Collaboration-server database and authentication dependencies."""

from fastapi import Depends, HTTPException, Request

from backend import auth as auth_helpers
from backend import server_database as db


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
    payload = {"user": db.public_user(user), "capabilities": db.capabilities_for_user(user)}
    if token is not None:
        payload["token"] = token
    return payload


def public_user_payload(item: dict) -> dict:
    return {
        "id": item["id"], "username": item["username"], "role": item["role"],
        "created_at": str(item.get("created_at")) if item.get("created_at") else None,
        "password_changed_at": str(item.get("password_changed_at")) if item.get("password_changed_at") else None,
        "disabled_at": str(item.get("disabled_at")) if item.get("disabled_at") else None,
    }
