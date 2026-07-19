"""Environment context, current user lookup, and capability checks."""

from __future__ import annotations

import uuid

from fastapi import HTTPException

from .auth import ensure_builtin_admin, extract_bearer_token, session_token_hash
from .repositories import permissions as permission_repository


VALID_ENVIRONMENTS = {"research", "field"}
ALL_CAPABILITIES = {
    "manage_users",
    "change_own_password",
    "delete_models",
    "delete_aircraft",
    "delete_flights",
    "update_columns",
}


def get_app_context(conn, request=None) -> dict:
    """Return persisted app context, seeding defaults when missing."""
    environment = permission_repository.get_setting(conn, "environment")
    if environment not in VALID_ENVIRONMENTS:
        environment = "research"
        permission_repository.set_setting(conn, "environment", environment)

    node_id = permission_repository.get_setting(conn, "node_id")
    local_node_id = permission_repository.get_setting(conn, "local_node_id")
    if local_node_id and not node_id:
        node_id = local_node_id
    if not node_id:
        node_id = f"{environment}-{uuid.uuid4().hex[:12]}"
        permission_repository.set_setting(conn, "node_id", node_id)
    if not local_node_id:
        permission_repository.set_setting(conn, "local_node_id", node_id)

    ensure_builtin_admin(conn)

    return {"environment": environment, "node_id": node_id}


def set_app_context(conn, updates: dict) -> dict:
    environment = updates.get("environment")
    if environment is not None:
        if environment not in VALID_ENVIRONMENTS:
            raise ValueError("environment must be research or field")
        permission_repository.set_setting(conn, "environment", environment)

    node_id = updates.get("node_id")
    if node_id is not None:
        node_id = str(node_id).strip()
        if not node_id:
            raise ValueError("node_id must not be empty")
        current_node_id = (
            permission_repository.get_setting(conn, "local_node_id")
            or permission_repository.get_setting(conn, "node_id")
        )
        if current_node_id and node_id != current_node_id and _node_identity_is_in_use(conn):
            raise ValueError("node_id cannot be changed after synchronization has started")
        permission_repository.set_setting(conn, "node_id", node_id)
        permission_repository.set_setting(conn, "local_node_id", node_id)

    return get_app_context(conn)


def _node_identity_is_in_use(conn) -> bool:
    if any(
        permission_repository.get_setting(conn, key)
        for key in ("last_successful_push_at", "last_successful_pull_at", "last_pull_cursor")
    ):
        return True
    for table in ("aircraft_models", "aircraft", "flights"):
        if conn.execute(f"SELECT 1 FROM {table} WHERE server_id IS NOT NULL LIMIT 1").fetchone():
            return True
    return bool(conn.execute("SELECT 1 FROM sync_imports LIMIT 1").fetchone())


def get_current_user(conn, request):
    token = extract_bearer_token(request)
    if not token:
        return None

    return permission_repository.get_user_by_session_hash(conn, session_token_hash(token))


def get_capabilities(context: dict, user: dict | None) -> list[str]:
    if not user:
        return []

    caps = {"change_own_password"}
    if user.get("role") == "admin":
        caps.update({
            "manage_users",
            "delete_models",
            "delete_aircraft",
            "delete_flights",
            "update_columns",
        })
    return sorted(caps)


def require_capability(conn, request, capability: str) -> dict:
    if capability not in ALL_CAPABILITIES:
        raise HTTPException(500, f"Unknown capability: {capability}")

    context = get_app_context(conn, request)
    user = get_current_user(conn, request)
    if capability not in get_capabilities(context, user):
        raise HTTPException(403, f"Permission denied: {capability}")
    return user
