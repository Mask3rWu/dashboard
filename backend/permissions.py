"""Environment context, current user lookup, and capability checks."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import HTTPException

from .auth import ensure_builtin_admin, extract_bearer_token, session_token_hash


VALID_ENVIRONMENTS = {"research", "field"}
ALL_CAPABILITIES = {
    "manage_users",
    "change_own_password",
    "delete_models",
    "delete_aircraft",
    "delete_flights",
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_setting(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO app_settings (key, value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value, _now_text()),
    )


def get_app_context(conn, request=None) -> dict:
    """Return persisted app context, seeding defaults when missing."""
    environment = _get_setting(conn, "environment")
    if environment not in VALID_ENVIRONMENTS:
        environment = "research"
        _set_setting(conn, "environment", environment)

    node_id = _get_setting(conn, "node_id")
    if not node_id:
        node_id = f"{environment}-{uuid.uuid4().hex[:12]}"
        _set_setting(conn, "node_id", node_id)

    if environment == "research":
        ensure_builtin_admin(conn)

    return {"environment": environment, "node_id": node_id}


def set_app_context(conn, updates: dict) -> dict:
    environment = updates.get("environment")
    if environment is not None:
        if environment not in VALID_ENVIRONMENTS:
            raise ValueError("environment must be research or field")
        _set_setting(conn, "environment", environment)

    node_id = updates.get("node_id")
    if node_id is not None:
        node_id = str(node_id).strip()
        if not node_id:
            raise ValueError("node_id must not be empty")
        _set_setting(conn, "node_id", node_id)

    context = get_app_context(conn)
    if context["environment"] == "research":
        ensure_builtin_admin(conn)
    return context


def get_current_user(conn, request):
    token = extract_bearer_token(request)
    if not token:
        return None

    row = conn.execute(
        """SELECT u.id, u.username, u.role, u.created_at, u.password_changed_at
           FROM auth_sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token_hash=?
             AND (s.expires_at IS NULL OR s.expires_at > datetime('now'))""",
        (session_token_hash(token),),
    ).fetchone()
    return dict(row) if row else None


def get_capabilities(context: dict, user: dict | None) -> list[str]:
    if context.get("environment") != "research" or not user:
        return []

    caps = {"change_own_password", "delete_models", "delete_aircraft", "delete_flights"}
    if user.get("role") == "admin":
        caps.add("manage_users")
    return sorted(caps)


def require_capability(conn, request, capability: str) -> dict:
    if capability not in ALL_CAPABILITIES:
        raise HTTPException(500, f"Unknown capability: {capability}")

    context = get_app_context(conn, request)
    user = get_current_user(conn, request)
    if capability not in get_capabilities(context, user):
        raise HTTPException(403, f"Permission denied: {capability}")
    return user
