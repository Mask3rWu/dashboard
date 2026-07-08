"""Runtime configuration and local sync context helpers."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime

from . import sync_client
from . import permission_repository
from .database import DATA_DIR


def _env_bool(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_base_url(value: str | None) -> str:
    text = (value or "").strip()
    return text.rstrip("/")


def get_local_node_id(conn) -> str:
    local_node_id = permission_repository.get_setting(conn, "local_node_id")
    if not local_node_id:
        local_node_id = permission_repository.get_setting(conn, "node_id")
    if not local_node_id:
        import uuid

        local_node_id = f"node-{uuid.uuid4().hex[:8]}"
    permission_repository.set_setting(conn, "local_node_id", local_node_id)
    if not permission_repository.get_setting(conn, "node_id"):
        permission_repository.set_setting(conn, "node_id", local_node_id)
    return local_node_id


def get_server_base_url(conn) -> str:
    if os.environ.get("SERVER_BASE_URL") is not None:
        return _normalize_base_url(os.environ.get("SERVER_BASE_URL"))
    return _normalize_base_url(permission_repository.get_setting(conn, "server_base_url"))


def get_sync_enabled(conn) -> bool:
    env_value = _env_bool("SYNC_ENABLED")
    if env_value is not None:
        return env_value
    value = permission_repository.get_setting(conn, "sync_enabled")
    if value is None:
        return True
    return value.strip().lower() in {"1", "true", "yes", "on"}


def update_runtime_config(conn, updates: dict) -> None:
    if "data_dir" in updates and updates["data_dir"] is not None:
        requested = os.path.abspath(os.path.expanduser(str(updates["data_dir"])))
        if requested != DATA_DIR:
            raise ValueError("data_dir can only be changed before startup with the DATA_DIR environment variable")

    if "server_base_url" in updates and updates["server_base_url"] is not None:
        permission_repository.set_setting(
            conn, "server_base_url", _normalize_base_url(str(updates["server_base_url"]))
        )
    if "sync_enabled" in updates and updates["sync_enabled"] is not None:
        permission_repository.set_setting(conn, "sync_enabled", "true" if updates["sync_enabled"] else "false")


def _check_server(base_url: str, sync_enabled: bool) -> dict:
    checked_at = datetime.now().isoformat(timespec="seconds")
    if not sync_enabled:
        return {"server_reachable": False, "server_status": "disabled", "last_server_check_at": checked_at}
    if not base_url:
        return {"server_reachable": False, "server_status": "not_configured", "last_server_check_at": checked_at}

    health_url = f"{base_url}/health"
    try:
        with urllib.request.urlopen(health_url, timeout=2) as response:
            status_code = getattr(response, "status", response.getcode())
            body = response.read(1024 * 64).decode("utf-8", errors="replace")
        server_status = "online" if 200 <= status_code < 300 else "offline"
        return {
            "server_reachable": server_status == "online",
            "server_status": server_status,
            "last_server_check_at": checked_at,
            "server_health": _parse_json_object(body),
        }
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as e:
        return {
            "server_reachable": False,
            "server_status": "offline",
            "last_server_check_at": checked_at,
            "server_error": str(e),
        }


def _parse_json_object(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _count_flights_by_state(conn, state: str) -> int:
    row = conn.execute("SELECT COUNT(*) FROM flights WHERE sync_state=?", (state,)).fetchone()
    return int(row[0] or 0)


def _sync_summary(conn) -> dict:
    return {
        "pending_upload": (
            _count_flights_by_state(conn, "local_only")
            + _count_flights_by_state(conn, "pending_upload")
            + _count_flights_by_state(conn, "dirty")
        ),
        "upload_failed": _count_flights_by_state(conn, "upload_failed"),
        "conflict": _count_flights_by_state(conn, "conflict"),
        "last_push_at": permission_repository.get_setting(conn, "last_successful_push_at"),
        "last_pull_at": permission_repository.get_setting(conn, "last_successful_pull_at"),
    }


def _server_auth_context(base_url: str, sync_enabled: bool, token: str | None) -> dict:
    if not sync_enabled or not base_url or not token:
        return {"server_user": None, "server_capabilities": []}
    try:
        payload = sync_client.auth_me(base_url, token=token, timeout=2)
    except sync_client.SyncClientError:
        return {"server_user": None, "server_capabilities": []}
    return {
        "server_user": payload.get("user") if isinstance(payload.get("user"), dict) else None,
        "server_capabilities": payload.get("capabilities") if isinstance(payload.get("capabilities"), list) else [],
    }


def runtime_context(conn, server_token: str | None = None) -> dict:
    local_node_id = get_local_node_id(conn)
    server_base_url = get_server_base_url(conn)
    sync_enabled = get_sync_enabled(conn)
    server = _check_server(server_base_url, sync_enabled)
    server_auth = _server_auth_context(server_base_url, sync_enabled, server_token)
    return {
        "data_dir": DATA_DIR,
        "sync_enabled": sync_enabled,
        "server_base_url": server_base_url,
        **server,
        "local_node_id": local_node_id,
        **server_auth,
        "sync_summary": _sync_summary(conn),
    }
