"""MySQL server database helpers for the collaborative sync service."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

from .auth import hash_password, session_token_hash


DEFAULT_SERVER_DB_URL = "mysql+pymysql://flight:flight@127.0.0.1:3306/flight_analyzer"
SERVER_DB_URL = os.environ.get("SERVER_DB_URL", DEFAULT_SERVER_DB_URL)
SERVER_DATA_DIR = os.path.abspath(
    os.path.expanduser(os.environ.get("SERVER_DATA_DIR") or os.path.join(os.getcwd(), ".devdata", "server"))
)
SESSION_DAYS = 7

_ENGINE: Engine | None = None
_IDENTIFIER_RE = re.compile(r"^[^\W\d]\w*$", re.UNICODE)
_DATA_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_IDENTIFIER_LEN = 64


SERVER_CAPABILITIES = {
    "manage_users",
    "change_own_password",
    "delete_models",
    "delete_aircraft",
    "delete_flights",
    "resolve_conflicts",
    "sync_push",
    "sync_pull",
}


SCHEMA_DDL = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        username VARCHAR(128) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(32) NOT NULL,
        created_at DATETIME(6) NOT NULL,
        password_changed_at DATETIME(6) NULL,
        disabled_at DATETIME(6) NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_sessions (
        token_hash CHAR(64) PRIMARY KEY,
        user_id BIGINT NOT NULL,
        created_at DATETIME(6) NOT NULL,
        expires_at DATETIME(6) NULL,
        revoked_at DATETIME(6) NULL,
        INDEX idx_auth_sessions_user (user_id),
        CONSTRAINT fk_auth_sessions_user
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS aircraft_models (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        client_uid VARCHAR(64) NULL,
        source_node_id VARCHAR(64) NULL,
        name VARCHAR(255) NOT NULL UNIQUE,
        has_header TINYINT NOT NULL DEFAULT 1,
        has_uav_send_id TINYINT NOT NULL DEFAULT 0,
        extract_serial_from_path TINYINT NOT NULL DEFAULT 0,
        config_signature CHAR(64) NOT NULL,
        version BIGINT NOT NULL DEFAULT 1,
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        deleted_at DATETIME(6) NULL,
        UNIQUE KEY uniq_model_client_uid (client_uid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS aircraft (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        client_uid VARCHAR(64) NULL,
        source_node_id VARCHAR(64) NULL,
        model_id BIGINT NOT NULL,
        name VARCHAR(255) NOT NULL,
        version BIGINT NOT NULL DEFAULT 1,
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        deleted_at DATETIME(6) NULL,
        UNIQUE KEY uniq_aircraft_model_name (model_id, name),
        UNIQUE KEY uniq_aircraft_client_uid (client_uid),
        INDEX idx_aircraft_model (model_id),
        CONSTRAINT fk_aircraft_model
            FOREIGN KEY (model_id) REFERENCES aircraft_models(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS flights (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        client_uid VARCHAR(64) NULL,
        source_node_id VARCHAR(64) NULL,
        aircraft_id BIGINT NOT NULL,
        name VARCHAR(255) NOT NULL,
        source_path TEXT NULL,
        session_key VARCHAR(255) NOT NULL DEFAULT '',
        flight_date DATE NULL,
        start_time DATETIME(6) NULL,
        end_time DATETIME(6) NULL,
        duration_sec DOUBLE NULL,
        total_rows BIGINT NOT NULL DEFAULT 0,
        record_daily_duration_min DOUBLE NULL,
        record_batch_name VARCHAR(255) NOT NULL DEFAULT '',
        record_location VARCHAR(255) NOT NULL DEFAULT '',
        record_payload VARCHAR(255) NOT NULL DEFAULT '',
        record_weather VARCHAR(255) NOT NULL DEFAULT '',
        record_fuel_amount DOUBLE NULL,
        record_takeoff_weight DOUBLE NULL,
        record_altitude DOUBLE NULL,
        record_wind_speed DOUBLE NULL,
        record_note TEXT NOT NULL,
        version BIGINT NOT NULL DEFAULT 1,
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        deleted_at DATETIME(6) NULL,
        deleted_by BIGINT NULL,
        delete_reason VARCHAR(255) NULL,
        UNIQUE KEY uniq_flight_business (aircraft_id, flight_date, session_key),
        UNIQUE KEY uniq_flight_client_uid (client_uid),
        INDEX idx_flights_aircraft (aircraft_id),
        CONSTRAINT fk_flights_aircraft
            FOREIGN KEY (aircraft_id) REFERENCES aircraft(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS data_table_registry (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        model_id BIGINT NOT NULL,
        data_type_key VARCHAR(128) NOT NULL,
        table_name VARCHAR(255) NOT NULL,
        display_label VARCHAR(255) NOT NULL,
        file_patterns JSON NOT NULL,
        is_alert TINYINT NOT NULL DEFAULT 0,
        version BIGINT NOT NULL DEFAULT 1,
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        UNIQUE KEY uniq_model_datatype (model_id, data_type_key),
        UNIQUE KEY uniq_table_name (table_name),
        INDEX idx_dtr_model (model_id),
        CONSTRAINT fk_dtr_model
            FOREIGN KEY (model_id) REFERENCES aircraft_models(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS column_registry (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        model_id BIGINT NOT NULL,
        data_type_key VARCHAR(128) NOT NULL,
        table_name VARCHAR(255) NOT NULL,
        column_name VARCHAR(255) NOT NULL,
        display_label VARCHAR(255) NOT NULL,
        unit VARCHAR(64) NOT NULL DEFAULT '',
        data_type VARCHAR(32) NOT NULL DEFAULT 'REAL',
        ordinal INT NULL,
        is_numeric TINYINT NOT NULL DEFAULT 1,
        scale_factor DOUBLE NOT NULL DEFAULT 1.0,
        version BIGINT NOT NULL DEFAULT 1,
        created_at DATETIME(6) NOT NULL,
        updated_at DATETIME(6) NOT NULL,
        UNIQUE KEY uniq_model_table_column (model_id, table_name, column_name),
        INDEX idx_colreg_model_type (model_id, data_type_key),
        CONSTRAINT fk_colreg_model
            FOREIGN KEY (model_id) REFERENCES aircraft_models(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS flight_raw_files (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        flight_id BIGINT NOT NULL,
        original_name VARCHAR(255) NOT NULL,
        original_rel_path VARCHAR(1024) NOT NULL,
        storage_rel_path VARCHAR(1024) NOT NULL,
        sha256 CHAR(64) NOT NULL,
        size_bytes BIGINT NOT NULL,
        data_type_key VARCHAR(128) NULL,
        source_mtime DOUBLE NULL,
        created_at DATETIME(6) NOT NULL,
        UNIQUE KEY uniq_flight_raw (flight_id, storage_rel_path(255)),
        INDEX idx_flight_raw_flight (flight_id),
        INDEX idx_flight_raw_sha256 (sha256),
        CONSTRAINT fk_flight_raw_flight
            FOREIGN KEY (flight_id) REFERENCES flights(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_imports (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        package_id VARCHAR(64) NOT NULL,
        source_node_id VARCHAR(64) NOT NULL,
        imported_by BIGINT NULL,
        status VARCHAR(32) NOT NULL,
        report_json JSON NOT NULL,
        created_at DATETIME(6) NOT NULL,
        UNIQUE KEY uniq_package_source (package_id, source_node_id),
        INDEX idx_sync_imports_user (imported_by),
        CONSTRAINT fk_sync_imports_user
            FOREIGN KEY (imported_by) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_changes (
        `cursor` BIGINT PRIMARY KEY AUTO_INCREMENT,
        entity_type VARCHAR(64) NOT NULL,
        entity_id BIGINT NOT NULL,
        change_type VARCHAR(32) NOT NULL,
        entity_version BIGINT NOT NULL,
        changed_at DATETIME(6) NOT NULL,
        changed_by_node_id VARCHAR(64) NULL,
        package_id VARCHAR(64) NULL,
        INDEX idx_sync_changes_entity (entity_type, entity_id),
        INDEX idx_sync_changes_changed_at (changed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_clients (
        node_id VARCHAR(64) PRIMARY KEY,
        display_name VARCHAR(255) NULL,
        last_seen_at DATETIME(6) NULL,
        last_push_cursor BIGINT NULL,
        last_pull_cursor BIGINT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

def text(statement: str):
    from sqlalchemy import text as sqlalchemy_text

    return sqlalchemy_text(statement)


def is_integrity_error(exc: Exception) -> bool:
    try:
        from sqlalchemy.exc import IntegrityError
    except ImportError:
        return False
    return isinstance(exc, IntegrityError)


def integrity_error_detail(exc: Exception) -> str:
    return str(getattr(exc, "orig", exc))


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        from sqlalchemy import create_engine

        _ENGINE = create_engine(SERVER_DB_URL, pool_pre_ping=True, future=True)
    return _ENGINE


def utcnow() -> datetime:
    return datetime.utcnow()


def init_server_schema(engine: Engine | None = None) -> None:
    ensure_server_data_dir()
    engine = engine or get_engine()
    with engine.begin() as conn:
        for ddl in SCHEMA_DDL:
            conn.execute(text(ddl))
        ensure_builtin_admin(conn)
        from .model_seeds import apply_builtin_model_seeds_to_server

        apply_builtin_model_seeds_to_server(conn)


def ensure_server_data_dir() -> None:
    for rel in (
        ("raw_files",),
        ("incoming",),
        ("bundles",),
    ):
        os.makedirs(os.path.join(SERVER_DATA_DIR, *rel), exist_ok=True)


def ensure_builtin_admin(conn: Connection) -> None:
    row = conn.execute(
        text("SELECT id FROM users WHERE username=:username"),
        {"username": "admin"},
    ).first()
    if row:
        return
    now = utcnow()
    conn.execute(
        text(
            """INSERT INTO users (username, password_hash, role, created_at)
               VALUES (:username, :password_hash, 'admin', :created_at)"""
        ),
        {
            "username": "admin",
            "password_hash": hash_password("123456"),
            "created_at": now,
        },
    )


def capabilities_for_user(user: dict[str, Any] | None) -> list[str]:
    if not user:
        return []
    if user.get("role") == "admin":
        return sorted(SERVER_CAPABILITIES)
    return sorted({"change_own_password", "sync_push", "sync_pull"})


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "created_at": user.get("created_at"),
        "password_changed_at": user.get("password_changed_at"),
    }


def row_to_dict(row) -> dict[str, Any] | None:
    return dict(row._mapping) if row is not None else None


def get_user_by_username(conn: Connection, username: str) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            """SELECT id, username, password_hash, role, created_at,
                      password_changed_at, disabled_at
               FROM users
               WHERE username=:username"""
        ),
        {"username": username},
    ).first()
    return row_to_dict(row)


def get_user_by_session_token(conn: Connection, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    row = conn.execute(
        text(
            """SELECT u.id, u.username, u.password_hash, u.role, u.created_at,
                      u.password_changed_at, u.disabled_at
               FROM auth_sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash=:token_hash
                 AND s.revoked_at IS NULL
                 AND u.disabled_at IS NULL
                 AND (s.expires_at IS NULL OR s.expires_at > :now)"""
        ),
        {"token_hash": session_token_hash(token), "now": utcnow()},
    ).first()
    return row_to_dict(row)


def create_session(conn: Connection, user_id: int) -> str:
    import secrets

    token = secrets.token_urlsafe(32)
    conn.execute(
        text(
            """INSERT INTO auth_sessions
                 (token_hash, user_id, created_at, expires_at)
               VALUES (:token_hash, :user_id, :created_at, :expires_at)"""
        ),
        {
            "token_hash": session_token_hash(token),
            "user_id": user_id,
            "created_at": utcnow(),
            "expires_at": utcnow() + timedelta(days=SESSION_DAYS),
        },
    )
    return token


def revoke_session(conn: Connection, token: str) -> None:
    conn.execute(
        text("UPDATE auth_sessions SET revoked_at=:revoked_at WHERE token_hash=:token_hash"),
        {"revoked_at": utcnow(), "token_hash": session_token_hash(token)},
    )


def create_user(conn: Connection, username: str, password_hash: str, role: str) -> int:
    now = utcnow()
    conn.execute(
        text(
            """INSERT INTO users (username, password_hash, role, created_at)
               VALUES (:username, :password_hash, :role, :created_at)"""
        ),
        {
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "created_at": now,
        },
    )
    row = conn.execute(text("SELECT LAST_INSERT_ID() AS id")).first()
    return int(row._mapping["id"])


def update_user_password(conn: Connection, user_id: int, password_hash: str) -> None:
    now = utcnow()
    conn.execute(
        text(
            """UPDATE users
               SET password_hash=:password_hash, password_changed_at=:changed_at
               WHERE id=:user_id"""
        ),
        {"password_hash": password_hash, "changed_at": now, "user_id": user_id},
    )
    conn.execute(
        text("UPDATE auth_sessions SET revoked_at=:revoked_at WHERE user_id=:user_id"),
        {"revoked_at": now, "user_id": user_id},
    )


def require_capability(user: dict[str, Any] | None, capability: str) -> None:
    from fastapi import HTTPException

    if capability not in SERVER_CAPABILITIES:
        raise HTTPException(500, f"Unknown capability: {capability}")
    if capability not in capabilities_for_user(user):
        raise HTTPException(403, f"Permission denied: {capability}")


def validate_data_type_key(data_type_key: str) -> str:
    value = (data_type_key or "").strip()
    if not _DATA_TYPE_RE.match(value):
        raise ValueError(f"Invalid data_type_key: {data_type_key!r}")
    return value


def validate_identifier(identifier: str, label: str = "identifier") -> str:
    value = (identifier or "").strip()
    if "`" in value or "\x00" in value or not _IDENTIFIER_RE.match(value):
        raise ValueError(f"Invalid SQL {label}: {identifier!r}")
    if len(value) > _MAX_IDENTIFIER_LEN:
        raise ValueError(f"SQL {label} exceeds {_MAX_IDENTIFIER_LEN} characters: {identifier!r}")
    return value


def quote_identifier(identifier: str) -> str:
    return f"`{validate_identifier(identifier)}`"


def server_data_table_name(model_id: int, data_type_key: str) -> str:
    data_type_key = validate_data_type_key(data_type_key)
    table_name = f"server_data_m{int(model_id)}_{data_type_key}"
    return validate_identifier(table_name, "table name")


def mysql_type(config_type: str | None) -> str:
    value = (config_type or "TEXT").upper()
    if value in {"REAL", "FLOAT", "DOUBLE"}:
        return "DOUBLE"
    if value in {"INTEGER", "INT", "BOOL", "BOOLEAN"}:
        return "BIGINT"
    if value == "TEXT":
        return "TEXT"
    return "TEXT"


def normalize_column(col: dict[str, Any], fallback_ordinal: int) -> dict[str, Any]:
    column_name = validate_identifier(str(col.get("name") or col.get("column_name") or ""), "column name")
    display_label = str(col.get("label") or col.get("display_label") or column_name)
    data_type = str(col.get("type") or col.get("data_type") or "REAL")
    return {
        "name": column_name,
        "display_label": display_label,
        "unit": str(col.get("unit") or ""),
        "data_type": data_type,
        "ordinal": col.get("ordinal", fallback_ordinal),
        "is_numeric": 1 if mysql_type(data_type) in {"DOUBLE", "BIGINT"} else 0,
        "scale_factor": float(col.get("scale_factor", 1.0) or 1.0),
    }


def create_dynamic_table(
    conn: Connection,
    model_id: int,
    data_type_key: str,
    columns: list[dict[str, Any]],
) -> str:
    table_name = server_data_table_name(model_id, data_type_key)
    column_sql = [
        "`id` BIGINT PRIMARY KEY AUTO_INCREMENT",
        "`flight_id` BIGINT NOT NULL",
        "`time_str` VARCHAR(64) NULL",
        "`time_sec` DOUBLE NULL",
    ]
    seen = {"id", "flight_id", "time_str", "time_sec"}
    for index, raw_col in enumerate(columns, start=1):
        col = normalize_column(raw_col, index)
        if col["name"] in seen:
            raise ValueError(f"Duplicate dynamic column: {col['name']}")
        seen.add(col["name"])
        column_sql.append(f"{quote_identifier(col['name'])} {mysql_type(col['data_type'])} NULL")

    ddl = f"""
    CREATE TABLE IF NOT EXISTS {quote_identifier(table_name)} (
        {", ".join(column_sql)},
        INDEX idx_flight_time (flight_id, time_sec),
        INDEX idx_flight_id (flight_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    conn.execute(text(ddl))
    return table_name


def config_signature(model_payload: dict[str, Any]) -> str:
    encoded = json.dumps(model_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def create_model(conn: Connection, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Model name is required")

    data_types = payload.get("data_types") or {}
    if not isinstance(data_types, dict):
        raise ValueError("data_types must be an object")

    now = utcnow()
    model_config = {
        "has_header": bool(payload.get("has_header", True)),
        "has_uav_send_id": bool(payload.get("has_uav_send_id", False)),
        "extract_serial_from_path": bool(payload.get("extract_serial_from_path", False)),
        "data_types": data_types,
    }
    conn.execute(
        text(
            """INSERT INTO aircraft_models
                 (client_uid, source_node_id, name, has_header, has_uav_send_id,
                  extract_serial_from_path, config_signature, created_at, updated_at)
               VALUES
                 (:client_uid, :source_node_id, :name, :has_header, :has_uav_send_id,
                  :extract_serial_from_path, :config_signature, :created_at, :updated_at)"""
        ),
        {
            "client_uid": payload.get("client_uid"),
            "source_node_id": payload.get("source_node_id"),
            "name": name,
            "has_header": 1 if model_config["has_header"] else 0,
            "has_uav_send_id": 1 if model_config["has_uav_send_id"] else 0,
            "extract_serial_from_path": 1 if model_config["extract_serial_from_path"] else 0,
            "config_signature": config_signature(model_config),
            "created_at": now,
            "updated_at": now,
        },
    )
    model_id = int(conn.execute(text("SELECT LAST_INSERT_ID() AS id")).first()._mapping["id"])

    created_tables: list[str] = []
    for data_type_key, raw_def in data_types.items():
        dt_key = validate_data_type_key(data_type_key)
        dt_def = raw_def or {}
        columns = list(dt_def.get("columns") or [])
        table_name = create_dynamic_table(conn, model_id, dt_key, columns)
        created_tables.append(table_name)
        conn.execute(
            text(
                """INSERT INTO data_table_registry
                     (model_id, data_type_key, table_name, display_label, file_patterns,
                      is_alert, created_at, updated_at)
                   VALUES
                     (:model_id, :data_type_key, :table_name, :display_label, :file_patterns,
                      :is_alert, :created_at, :updated_at)"""
            ),
            {
                "model_id": model_id,
                "data_type_key": dt_key,
                "table_name": table_name,
                "display_label": str(dt_def.get("display_label") or dt_key),
                "file_patterns": json.dumps(dt_def.get("file_patterns") or [], ensure_ascii=False),
                "is_alert": 1 if dt_def.get("is_alert") else 0,
                "created_at": now,
                "updated_at": now,
            },
        )
        for index, raw_col in enumerate(columns, start=1):
            col = normalize_column(raw_col, index)
            conn.execute(
                text(
                    """INSERT INTO column_registry
                         (model_id, data_type_key, table_name, column_name, display_label,
                          unit, data_type, ordinal, is_numeric, scale_factor, created_at, updated_at)
                       VALUES
                         (:model_id, :data_type_key, :table_name, :column_name, :display_label,
                          :unit, :data_type, :ordinal, :is_numeric, :scale_factor,
                          :created_at, :updated_at)"""
                ),
                {
                    "model_id": model_id,
                    "data_type_key": dt_key,
                    "table_name": table_name,
                    "column_name": col["name"],
                    "display_label": col["display_label"],
                    "unit": col["unit"],
                    "data_type": col["data_type"],
                    "ordinal": col["ordinal"],
                    "is_numeric": col["is_numeric"],
                    "scale_factor": col["scale_factor"],
                    "created_at": now,
                    "updated_at": now,
                },
            )

    conn.execute(
        text(
            """INSERT INTO sync_changes
                 (entity_type, entity_id, change_type, entity_version, changed_at,
                  changed_by_node_id, package_id)
               VALUES ('aircraft_model', :entity_id, 'create', 1, :changed_at,
                       :changed_by_node_id, NULL)"""
        ),
        {
            "entity_id": model_id,
            "changed_at": now,
            "changed_by_node_id": payload.get("source_node_id"),
        },
    )
    return {"id": model_id, "name": name, "dynamic_tables": created_tables}
