"""Explicitly synchronize collaboration-server model definitions locally."""

from __future__ import annotations

from typing import Any

from backend.import_pipeline.format_configs import (
    build_model_config_from_db,
    register_model_tables,
)
from backend.sync.protocol import model_structure_signature


class ModelSyncConflict(ValueError):
    """The remote model cannot be linked to the existing local definition."""


def annotate_remote_models(conn, payload: dict[str, Any]) -> dict[str, Any]:
    models = payload.get("models") if isinstance(payload.get("models"), list) else []
    server_ids = [int(model["id"]) for model in models if model.get("id") is not None]
    local_by_server: dict[int, int] = {}
    if server_ids:
        placeholders = ",".join("?" for _ in server_ids)
        rows = conn.execute(
            f"""SELECT id, server_id FROM aircraft_models
                WHERE server_id IN ({placeholders})
                  AND deleted_at IS NULL AND server_deleted_at IS NULL""",
            server_ids,
        ).fetchall()
        local_by_server = {int(row["server_id"]): int(row["id"]) for row in rows}
    for model in models:
        server_id = int(model["id"])
        model["model_synced"] = server_id in local_by_server
        model["local_model_id"] = local_by_server.get(server_id)
    return payload


def _matching_local_model(conn, model: dict[str, Any]):
    server_id = int(model["id"])
    existing = conn.execute(
        "SELECT * FROM aircraft_models WHERE server_id=?", (server_id,)
    ).fetchone()
    if existing:
        return existing, "server_id"
    client_uid = model.get("client_uid")
    if client_uid:
        existing = conn.execute(
            "SELECT * FROM aircraft_models WHERE client_uid=?", (str(client_uid),)
        ).fetchone()
        if existing:
            return existing, "client_uid"
    existing = conn.execute(
        "SELECT * FROM aircraft_models WHERE name=?", (str(model.get("name") or ""),)
    ).fetchone()
    return (existing, "name") if existing else (None, None)


def sync_model_definition(conn, payload: dict[str, Any]) -> dict[str, Any]:
    model = payload.get("model") if isinstance(payload.get("model"), dict) else None
    if not model or model.get("id") is None:
        raise ValueError("服务器返回的机型定义无效")
    config = model.get("config") if isinstance(model.get("config"), dict) else None
    if not config or not isinstance(config.get("data_types"), dict):
        raise ValueError("服务器机型缺少数据类型配置")

    server_id = int(model["id"])
    name = str(model.get("name") or "").strip()
    if not name:
        raise ValueError("服务器机型名称为空")
    existing, matched_by = _matching_local_model(conn, model)
    action = "created"

    if existing:
        local_id = int(existing["id"])
        if existing["server_id"] is not None and int(existing["server_id"]) != server_id:
            raise ModelSyncConflict(
                f"本地机型“{existing['name']}”已关联另一服务器机型，不能重新关联"
            )
        name_owner = conn.execute(
            "SELECT id FROM aircraft_models WHERE name=? AND id<>?",
            (name, local_id),
        ).fetchone()
        if name_owner:
            raise ModelSyncConflict(
                f"服务器机型名称“{name}”已被另一个本地机型使用，不能直接覆盖"
            )
        local_signature = model_structure_signature(build_model_config_from_db(conn, local_id))
        remote_signature = model_structure_signature(config)
        if local_signature != remote_signature:
            raise ModelSyncConflict(
                f"本地已存在同名或同标识机型“{existing['name']}”，但数据结构与服务器不一致；"
                "请先处理该本地机型，不能直接覆盖"
            )
        conn.execute(
            """UPDATE aircraft_models
               SET server_id=?, client_uid=COALESCE(client_uid, ?),
                   source_node_id=?, sync_origin='server', sync_state='server_cache',
                   server_version=?, last_sync_at=datetime('now','localtime'),
                   sync_error_json=NULL, name=?, has_header=?, has_uav_send_id=?,
                   extract_serial_from_path=?, updated_at=COALESCE(?, updated_at),
                   server_deleted_at=NULL
               WHERE id=?""",
            (
                server_id,
                model.get("client_uid"),
                model.get("source_node_id") or "server",
                model.get("version") or 1,
                name,
                1 if config.get("has_header", True) else 0,
                1 if config.get("has_uav_send_id", False) else 0,
                1 if config.get("extract_serial_from_path", False) else 0,
                model.get("updated_at"),
                local_id,
            ),
        )
        action = "linked" if matched_by != "server_id" else "updated"
    else:
        conn.execute(
            """INSERT INTO aircraft_models
               (client_uid, server_id, source_node_id, sync_origin, sync_state,
                server_version, last_sync_at, name, has_header, has_uav_send_id,
                extract_serial_from_path, created_at, updated_at)
               VALUES (?, ?, ?, 'server', 'server_cache', ?,
                       datetime('now','localtime'), ?, ?, ?, ?,
                       COALESCE(?, datetime('now','localtime')),
                       COALESCE(?, datetime('now','localtime')))""",
            (
                model.get("client_uid"),
                server_id,
                model.get("source_node_id") or "server",
                model.get("version") or 1,
                name,
                1 if config.get("has_header", True) else 0,
                1 if config.get("has_uav_send_id", False) else 0,
                1 if config.get("extract_serial_from_path", False) else 0,
                model.get("created_at"),
                model.get("updated_at"),
            ),
        )
        local_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    register_model_tables(conn, local_id, config=config, commit=False)
    conn.commit()
    return {
        "ok": True,
        "status": "success",
        "action": action,
        "server_model_id": server_id,
        "local_model_id": local_id,
        "model": {"id": local_id, "name": name, "server_id": server_id},
    }
