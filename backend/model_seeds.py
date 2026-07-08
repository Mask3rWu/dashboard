"""Built-in aircraft model seeds for offline-first packaged installs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any

from .format_configs import register_model_tables


logger = logging.getLogger(__name__)
SEED_FILENAME = "builtin_model_seeds.json"


def _seed_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), SEED_FILENAME)


def _stable_client_uid(name: str, config: dict[str, Any]) -> str:
    payload = json.dumps({"name": name, "config": config}, ensure_ascii=False, sort_keys=True)
    return "builtin_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _load_seed_file() -> tuple[str, dict[str, Any]] | None:
    path = _seed_path()
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{SEED_FILENAME} must contain a JSON object")
    return path, data


def _find_existing_model(conn, *, server_id: int | None, client_uid: str | None, name: str):
    if server_id is not None:
        row = conn.execute("SELECT id FROM aircraft_models WHERE server_id=?", (server_id,)).fetchone()
        if row:
            return row
    if client_uid:
        row = conn.execute("SELECT id FROM aircraft_models WHERE client_uid=?", (client_uid,)).fetchone()
        if row:
            return row
    return conn.execute("SELECT id FROM aircraft_models WHERE name=?", (name,)).fetchone()


def apply_builtin_model_seeds(conn) -> dict[str, Any]:
    """Create missing built-in model definitions in the local SQLite database."""

    env_enabled = os.environ.get("BUILTIN_MODEL_SEEDS_ENABLED")
    if env_enabled is not None and env_enabled.strip().lower() in {"0", "false", "no", "off"}:
        return {"seed_file": None, "created": 0, "skipped": 0, "models": 0, "disabled": True}

    row = conn.execute(
        "SELECT value FROM app_settings WHERE key='builtin_model_seeds_enabled'"
    ).fetchone()
    if row and str(row[0]).strip().lower() in {"0", "false", "no", "off"}:
        return {"seed_file": None, "created": 0, "skipped": 0, "models": 0, "disabled": True}

    loaded = _load_seed_file()
    if loaded is None:
        return {"seed_file": None, "created": 0, "skipped": 0, "models": 0}

    path, data = loaded
    models = data.get("models") or []
    if not isinstance(models, list):
        raise ValueError(f"{SEED_FILENAME}.models must be a list")

    created = 0
    skipped = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for item in models:
        if not isinstance(item, dict):
            skipped += 1
            continue
        name = str(item.get("name") or "").strip()
        config = item.get("config") or {}
        if not name or not isinstance(config, dict):
            skipped += 1
            continue

        server_id = item.get("server_id")
        try:
            server_id = int(server_id) if server_id not in (None, "") else None
        except (TypeError, ValueError):
            server_id = None
        client_uid = str(item.get("client_uid") or "").strip() or None
        if not client_uid:
            client_uid = _stable_client_uid(name, config)

        if _find_existing_model(conn, server_id=server_id, client_uid=client_uid, name=name):
            skipped += 1
            continue

        conn.execute(
            """INSERT INTO aircraft_models
               (client_uid, server_id, source_node_id, sync_origin, sync_state,
                server_version, last_sync_at, name, has_header, has_uav_send_id,
                extract_serial_from_path, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                client_uid,
                server_id,
                item.get("source_node_id") or data.get("source_node_id") or "builtin_seed",
                "server" if server_id is not None else "local",
                "server_cache" if server_id is not None else "local_only",
                int(item.get("server_version") or 1),
                now if server_id is not None else None,
                name,
                1 if config.get("has_header", True) else 0,
                1 if config.get("has_uav_send_id", False) else 0,
                1 if config.get("extract_serial_from_path", False) else 0,
                item.get("created_at") or now,
                item.get("updated_at") or now,
            ),
        )
        model_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        register_model_tables(conn, model_id, config=config, commit=False)
        created += 1

    seed_hash = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    conn.execute(
        """INSERT INTO app_settings (key, value, updated_at)
           VALUES ('builtin_model_seed_hash', ?, datetime('now','localtime'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (seed_hash,),
    )
    logger.info("Applied builtin model seeds from %s: created=%s skipped=%s", path, created, skipped)
    return {"seed_file": path, "created": created, "skipped": skipped, "models": len(models)}


def apply_builtin_model_seeds_to_server(conn) -> dict[str, Any]:
    """Create missing built-in model definitions in the server MySQL database."""

    if os.environ.get("SERVER_BUILTIN_MODEL_SEEDS_ENABLED", "true").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return {"seed_file": None, "created": 0, "skipped": 0, "models": 0, "disabled": True}

    loaded = _load_seed_file()
    if loaded is None:
        return {"seed_file": None, "created": 0, "skipped": 0, "models": 0}

    from . import server_database as db

    path, data = loaded
    models = data.get("models") or []
    if not isinstance(models, list):
        raise ValueError(f"{SEED_FILENAME}.models must be a list")

    created = 0
    skipped = 0
    for item in models:
        if not isinstance(item, dict):
            skipped += 1
            continue
        name = str(item.get("name") or "").strip()
        config = item.get("config") or {}
        if not name or not isinstance(config, dict):
            skipped += 1
            continue

        client_uid = str(item.get("client_uid") or "").strip() or _stable_client_uid(name, config)
        existing = conn.execute(
            db.text(
                """SELECT id FROM aircraft_models
                   WHERE client_uid=:client_uid OR name=:name
                   LIMIT 1"""
            ),
            {"client_uid": client_uid, "name": name},
        ).first()
        if existing:
            skipped += 1
            continue

        db.create_model(
            conn,
            {
                "name": name,
                "client_uid": client_uid,
                "source_node_id": item.get("source_node_id") or data.get("source_node_id") or "builtin_seed",
                "has_header": bool(config.get("has_header", True)),
                "has_uav_send_id": bool(config.get("has_uav_send_id", False)),
                "extract_serial_from_path": bool(config.get("extract_serial_from_path", False)),
                "data_types": config.get("data_types") or {},
            },
        )
        created += 1

    logger.info("Applied server builtin model seeds from %s: created=%s skipped=%s", path, created, skipped)
    return {"seed_file": path, "created": created, "skipped": skipped, "models": len(models)}
