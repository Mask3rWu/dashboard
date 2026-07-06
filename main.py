"""Flight Analyzer — FastAPI backend + pywebview desktop shell."""

import os
import sys
import json
import threading
import time as _time
import traceback
import ctypes

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add parent to path for PyInstaller compatibility
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Ensure BASE_DIR and backend are importable
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.config import load_app_config

CONFIG_PATH = load_app_config()

from backend.database import init_db, get_db, DATA_DIR, DB_PATH, DB_BACKEND
from backend.parser import import_session
from backend import auth as auth_helpers
from backend.permissions import (
    get_app_context,
    set_app_context,
    get_current_user,
    get_capabilities,
    require_capability,
)
from backend.format_configs import (
    register_model_tables, get_columns_for_model,
    save_model_config_to_db, generate_config_from_scan,
    update_column_metadata, data_table_name,
)
from backend.scanner import scan_folder_sessions
from backend.raw_storage import get_raw_files_for_flight, build_flight_manifest
from backend.sync_package import export_package
from backend.sync_import import preview_import, import_package, import_pull_bundle, get_import_report
from backend import flight_repository, user_repository, sync_repository, sync_client
from backend import runtime_context
from backend import analysis

from datetime import datetime


# ─── Serve Frontend ────────────────────────────────────────

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend", "dist")


# ─── Startup Logging ─────────────────────────────────────────

STARTUP_LOG_PATH = os.path.join(DATA_DIR, "startup.log")


def _startup_log(msg):
    """Append a timestamped message to the startup log."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        with open(STARTUP_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        # Startup logging must never prevent the app from starting.
        pass


# ─── App Setup ─────────────────────────────────────────────

app = FastAPI(title="Flight Analyzer", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Log unhandled API errors and return a structured response."""
    _startup_log(
        f"UNHANDLED API ERROR {request.method} {request.url.path}: "
        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    )
    detail = str(exc) or "Internal Server Error"
    if len(detail) > 1000:
        detail = detail[:1000] + "..."
    return JSONResponse(
        status_code=500,
        content={"detail": detail, "error_type": type(exc).__name__},
    )


@app.get("/api/health")
def health_check():
    """Health check used by the frontend before loading data."""
    return {
        "status": "ok",
        "version": app.version,
        "data_dir": DATA_DIR,
        "db_backend": DB_BACKEND,
        "db_path": DB_PATH,
        "db_exists": os.path.exists(DB_PATH),
        "frontend_dir_exists": os.path.isdir(FRONTEND_DIR),
    }


# ─── Pydantic Models ───────────────────────────────────────

class ImportRequest(BaseModel):
    source_path: str


class ImportSessionRequest(BaseModel):
    source_path: str
    aircraft_id: int       # required — aircraft.id
    session_key: str = ''  # empty = import all sessions for this aircraft
    flight_date: str | None = None
    record_daily_duration_min: float | None = None
    record_batch_name: str | None = ''
    record_location: str | None = ''
    record_payload: str | None = ''
    record_weather: str | None = ''
    record_fuel_amount: float | None = None
    record_takeoff_weight: float | None = None
    record_altitude: float | None = None
    record_wind_speed: float | None = None
    record_note: str | None = ''


class UpdateFlightRequest(BaseModel):
    name: str


class FlightRecordRequest(BaseModel):
    record_daily_duration_min: float | None = None
    record_batch_name: str | None = None
    record_location: str | None = None
    record_payload: str | None = None
    record_weather: str | None = None
    record_fuel_amount: float | None = None
    record_takeoff_weight: float | None = None
    record_altitude: float | None = None
    record_wind_speed: float | None = None
    record_note: str | None = None


def _parse_raw_warnings(value: str | None):
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


class CreateModelRequest(BaseModel):
    name: str


class CreateModelFromScanRequest(BaseModel):
    name: str
    source_path: str
    # Optional allowlist of data_type_keys to register. When omitted, every
    # discovered type is registered. When provided, only the listed types are
    # kept (lets the user drop raw byte dumps during new-model creation).
    selected_data_types: list[str] | None = None


class UpdateModelRequest(BaseModel):
    name: str


class UpdateColumnRequest(BaseModel):
    display_label: str | None = None
    unit: str | None = None
    scale_factor: float | None = None


class UpdateDataTypeLabelRequest(BaseModel):
    display_label: str


class CreateAircraftRequest(BaseModel):
    name: str


class UpdateAircraftRequest(BaseModel):
    name: str


class AlignedRequest(BaseModel):
    column_keys: list[str]
    ref_table: str | None = None
    tolerance: float | None = None
    filter: dict | None = None


class CorrelationRequest(BaseModel):
    column_keys: list[str]


class AnomalyRequest(BaseModel):
    column_key: str
    window_size: int = 30
    sigma: float = 3.0


class CompareRequest(BaseModel):
    flight_ids: list[int]
    column_key: str


RECORD_TEXT_FIELDS = {
    "record_batch_name",
    "record_location",
    "record_payload",
    "record_weather",
    "record_note",
}
RECORD_NUMERIC_FIELDS = {
    "record_daily_duration_min",
    "record_fuel_amount",
    "record_takeoff_weight",
    "record_altitude",
    "record_wind_speed",
}
RECORD_FIELDS = RECORD_TEXT_FIELDS | RECORD_NUMERIC_FIELDS


def _model_dump(obj, exclude_unset=False):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_unset=exclude_unset)
    return obj.dict(exclude_unset=exclude_unset)


def _normalize_record_fields(data: dict, include_unset: bool = True) -> dict:
    if include_unset:
        keys = RECORD_FIELDS
    else:
        keys = {key for key in RECORD_FIELDS if key in data}

    normalized = {}
    for key in keys:
        value = data.get(key)
        if key in RECORD_TEXT_FIELDS:
            normalized[key] = (value or "").strip() if isinstance(value, str) else ""
        else:
            normalized[key] = value
    return normalized


def _normalize_flight_date(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "飞行日期格式必须为 YYYY-MM-DD")


class PresetCreate(BaseModel):
    model_id: int
    name: str
    columns: list[str]


class FilterPresetCreate(BaseModel):
    model_id: int
    name: str
    config: dict


class AppContextUpdate(BaseModel):
    environment: str | None = None
    node_id: str | None = None


class RuntimeConfigUpdate(BaseModel):
    data_dir: str | None = None
    server_base_url: str | None = None
    sync_enabled: bool | None = None


class ServerLoginRequest(BaseModel):
    username: str
    password: str


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


class SyncExportRequest(BaseModel):
    flight_ids: list[int]


class SyncPushBatchRequest(BaseModel):
    flight_ids: list[int] | None = None
    server_token: str | None = None


class SyncPullRequest(BaseModel):
    since: str | None = None
    server_token: str | None = None


class SyncRunRequest(BaseModel):
    flight_ids: list[int] | None = None
    since: str | None = None
    server_token: str | None = None


class SyncAbandonRequest(BaseModel):
    flight_ids: list[int]


class DeleteEntityRequest(BaseModel):
    scope: str = "auto"
    reason: str | None = None
    server_token: str | None = None


class SyncImportPreviewRequest(BaseModel):
    package_path: str


class SyncModelAction(BaseModel):
    source_model_id: int
    action: str
    target_model_id: int | None = None
    name: str | None = None


class SyncAircraftMapping(BaseModel):
    source_aircraft_id: int
    action: str
    target_aircraft_id: int | None = None
    name: str | None = None


class SyncImportRequest(BaseModel):
    package_path: str
    model_actions: list[SyncModelAction] = []
    aircraft_mappings: list[SyncAircraftMapping] = []
    conflict_policy: str = "skip"


def _public_user(user):
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "created_at": user.get("created_at"),
        "password_changed_at": user.get("password_changed_at"),
    }


def _context_payload(conn, request: Request, user=None):
    context = get_app_context(conn, request)
    if user is None:
        user = get_current_user(conn, request)
    return {
        **context,
        "user": _public_user(user),
        "capabilities": get_capabilities(context, user),
    }


# ─── App Context / Auth Routes ─────────────────────────────

@app.get("/api/app/context")
def get_app_context_api(request: Request):
    conn = get_db()
    try:
        payload = _context_payload(conn, request)
        conn.commit()
        return payload
    finally:
        conn.close()


@app.patch("/api/app/context")
def patch_app_context(req: AppContextUpdate, request: Request):
    conn = get_db()
    try:
        set_app_context(conn, req.dict(exclude_none=True))
        payload = _context_payload(conn, request)
        conn.commit()
        return payload
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    conn = get_db()
    try:
        get_app_context(conn, request)

        row = user_repository.get_user_by_username(conn, req.username.strip())
        if not row or not auth_helpers.verify_password(req.password, row["password_hash"]):
            raise HTTPException(401, "用户名或密码不正确")

        token = auth_helpers.create_session(conn, row["id"])
        user = {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "created_at": row["created_at"],
            "password_changed_at": row["password_changed_at"],
        }
        conn.commit()
        return {**_context_payload(conn, request, user=user), "token": token}
    finally:
        conn.close()


@app.post("/api/auth/logout")
def logout(request: Request):
    token = auth_helpers.extract_bearer_token(request)
    conn = get_db()
    try:
        if token:
            auth_helpers.delete_session(conn, token)
            conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/auth/me")
def auth_me(request: Request):
    conn = get_db()
    try:
        payload = _context_payload(conn, request)
        conn.commit()
        return payload
    finally:
        conn.close()


@app.post("/api/auth/change-password")
def change_password(req: ChangePasswordRequest, request: Request):
    if len(req.new_password) < 6:
        raise HTTPException(400, "新密码至少 6 位")

    conn = get_db()
    try:
        user = require_capability(conn, request, "change_own_password")
        auth_helpers.change_password(conn, user["id"], req.old_password, req.new_password)
        conn.commit()
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.post("/api/users")
def create_user_api(req: CreateUserRequest, request: Request):
    username = req.username.strip()
    if not username:
        raise HTTPException(400, "用户名不能为空")
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if req.role not in ("admin", "user"):
        raise HTTPException(400, "角色必须是 admin 或 user")

    conn = get_db()
    try:
        require_capability(conn, request, "manage_users")
        user_id = auth_helpers.create_user(conn, username, req.password, req.role)
        conn.commit()
        return {"id": user_id, "username": username, "role": req.role}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(400, f"用户 '{username}' 已存在")
        raise
    finally:
        conn.close()


# ─── Folder Browser ────────────────────────────────────────

@app.get("/api/folders/browse")
def browse_folder():
    """Open native folder picker dialog via tkinter."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        selected_path = filedialog.askdirectory(
            title='选择飞行数据文件夹',
            mustexist=True,
        )
        root.destroy()
        if selected_path and os.path.isdir(selected_path):
            return {'path': selected_path}
        return {'path': '', 'cancelled': True}
    except Exception as e:
        raise HTTPException(500, f"Folder browser failed: {e}")


@app.get("/api/folders/subdirs")
def list_subdirs(path: str):
    """List immediate subdirectories of a given path."""
    p = os.path.normpath(path)
    if not os.path.isdir(p):
        raise HTTPException(400, "Not a directory")
    try:
        entries = [name for name in sorted(os.listdir(p))
                   if os.path.isdir(os.path.join(p, name))]
        return {'path': p, 'subdirs': entries}
    except PermissionError:
        raise HTTPException(403, "Permission denied")


# ─── Model Routes ──────────────────────────────────────────

@app.get("/api/models")
def list_models():
    """List all aircraft models."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT am.*,
               (SELECT COUNT(*) FROM aircraft a
                WHERE a.model_id = am.id
                  AND a.deleted_at IS NULL AND a.server_deleted_at IS NULL) as aircraft_count,
               COALESCE((SELECT COUNT(*) FROM flights f
                         JOIN aircraft a2 ON a2.id = f.aircraft_id
                         WHERE a2.model_id = am.id
                           AND f.deleted_at IS NULL AND f.server_deleted_at IS NULL
                           AND a2.deleted_at IS NULL AND a2.server_deleted_at IS NULL), 0) as total_flights,
               COALESCE((SELECT SUM(f2.duration_sec) FROM flights f2
                         JOIN aircraft a3 ON a3.id = f2.aircraft_id
                         WHERE a3.model_id = am.id
                           AND f2.deleted_at IS NULL AND f2.server_deleted_at IS NULL
                           AND a3.deleted_at IS NULL AND a3.server_deleted_at IS NULL), 0) as total_flight_hours
               FROM aircraft_models am
               WHERE am.deleted_at IS NULL AND am.server_deleted_at IS NULL
               ORDER BY am.created_at"""
        ).fetchall()
        return {"models": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/models")
def create_model(req: CreateModelRequest):
    """Create a new aircraft model. Automatically creates data tables and column registry."""
    if not req.name.strip():
        raise HTTPException(400, "Model name must not be empty")

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO aircraft_models (name) VALUES (?)",
            (req.name,)
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        register_model_tables(conn, model_id)
        return {"id": model_id, "name": req.name}
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(400, f"Model name '{req.name}' already exists")
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.post("/api/models/from-scan")
def create_model_from_scan(req: CreateModelFromScanRequest):
    """Create a model from a scanned folder.

    Auto-generates a format config from the folder's file structure, then
    persists it. If ``selected_data_types`` is provided, only those data types
    are registered — raw byte dumps the user deselected during creation are
    dropped here. When omitted, every discovered type is kept.
    """
    conn = get_db()
    try:
        config_data = generate_config_from_scan(req.source_path)

        if req.selected_data_types is not None:
            keep = set(req.selected_data_types)
            config_data['data_types'] = {
                k: v for k, v in config_data['data_types'].items() if k in keep
            }

        if not config_data.get('data_types'):
            raise HTTPException(400, "No data types selected; cannot create an empty model")

        conn.execute(
            "INSERT INTO aircraft_models (name) VALUES (?)",
            (req.name,)
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        save_model_config_to_db(conn, model_id, config_data)
        register_model_tables(conn, model_id, config=config_data)
        conn.commit()
        return {"id": model_id, "name": req.name}
    except HTTPException:
        raise
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(400, f"Model name '{req.name}' already exists")
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.patch("/api/models/{model_id}")
def update_model(model_id: int, req: UpdateModelRequest):
    """Rename an aircraft model."""
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM aircraft_models WHERE id=?", (model_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Model not found")
        conn.execute(
            """UPDATE aircraft_models
               SET name=?,
                   sync_state=CASE WHEN sync_state IN ('synced', 'server_cache') THEN 'dirty' ELSE sync_state END
               WHERE id=?""",
            (req.name.strip(), model_id),
        )
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.delete("/api/models/{model_id}")
def delete_model(model_id: int, request: Request, req: DeleteEntityRequest | None = None):
    """Delete a model and all related aircraft, flights, and data (cascade).
    Also deletes the per-model format config file from disk."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM aircraft_models WHERE id=?", (model_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Model not found")
        scope = _delete_scope(row, req.scope if req else "auto")
        if scope == "server":
            server_id = row["server_id"]
            if server_id is None:
                raise HTTPException(400, "Model has not been synced to the server")
            result = _server_delete(conn, request, req, "model", int(server_id))
            _mark_local_server_deleted(conn, "model", model_id, result)
            conn.commit()
            return {"ok": True, "scope": "server", "server": result}
        _require_local_delete_capability(conn, request, "model")
        if scope == "local_unsynced" and row["server_id"] is not None:
            raise HTTPException(400, "Server-backed model cannot be deleted as local_unsynced")
        _delete_local_model(conn, model_id)
        conn.commit()
        return {"ok": True, "scope": scope}
    finally:
        conn.close()


# ─── Model Export / Import ───────────────────────────────────

class ImportModelRequest(BaseModel):
    name: str
    data: dict  # the export JSON


@app.get("/api/models/{model_id}/export")
def export_model(model_id: int):
    """Export a model's complete configuration as a JSON file.

    Saves to the application directory (next to exe in frozen mode,
    project root in dev mode). Returns the file path so the user can
    copy it to another machine.
    """
    conn = get_db()
    try:
        model = conn.execute(
            "SELECT name, has_header, has_uav_send_id, extract_serial_from_path "
            "FROM aircraft_models WHERE id=?",
            (model_id,)
        ).fetchone()
        if not model:
            raise HTTPException(404, "Model not found")

        # ── Data types + columns ──
        data_types = {}
        dtr_rows = conn.execute(
            "SELECT data_type_key, display_label, file_patterns, is_alert "
            "FROM data_table_registry WHERE model_id=? ORDER BY data_type_key",
            (model_id,)
        ).fetchall()
        for dtr in dtr_rows:
            dt_key = dtr['data_type_key']
            try:
                patterns = json.loads(dtr['file_patterns'] or '[]')
            except (json.JSONDecodeError, TypeError):
                patterns = []

            col_rows = conn.execute(
                "SELECT column_name, display_label, unit, data_type, ordinal, scale_factor "
                "FROM column_registry "
                "WHERE model_id=? AND data_type_key=? ORDER BY ordinal",
                (model_id, dt_key)
            ).fetchall()

            columns = []
            for cr in col_rows:
                columns.append({
                    'name': cr['column_name'],
                    'label': cr['display_label'],
                    'unit': cr['unit'] or '',
                    'type': cr['data_type'] or 'REAL',
                    'ordinal': cr['ordinal'],
                    'scale_factor': cr['scale_factor'] if cr['scale_factor'] is not None else 1.0,
                })

            data_types[dt_key] = {
                'display_label': dtr['display_label'],
                'file_patterns': patterns,
                'is_alert': bool(dtr['is_alert']),
                'columns': columns,
            }

        # ── Presets ──
        presets = []
        preset_rows = conn.execute(
            "SELECT name, columns_json FROM presets WHERE model_id=? ORDER BY name",
            (model_id,)
        ).fetchall()
        for pr in preset_rows:
            try:
                cols = json.loads(pr['columns_json'] or '[]')
            except (json.JSONDecodeError, TypeError):
                cols = []
            presets.append({'name': pr['name'], 'columns': cols})

        # ── Filter presets ──
        filter_presets = []
        fp_rows = conn.execute(
            "SELECT name, config_json FROM filter_presets WHERE model_id=? ORDER BY name",
            (model_id,)
        ).fetchall()
        for fp in fp_rows:
            try:
                cfg = json.loads(fp['config_json'] or '{}')
            except (json.JSONDecodeError, TypeError):
                cfg = {}
            filter_presets.append({'name': fp['name'], 'config': cfg})

        export = {
            'version': 1,
            'exported_at': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            'model': {
                'name': model['name'],
                'has_header': bool(model['has_header']),
                'has_uav_send_id': bool(model['has_uav_send_id']),
                'extract_serial_from_path': bool(model['extract_serial_from_path']),
            },
            'data_types': data_types,
            'presets': presets,
            'filter_presets': filter_presets,
        }

        # Save to disk next to the executable / project root
        if getattr(sys, 'frozen', False):
            out_dir = os.path.dirname(sys.executable)
        else:
            out_dir = BASE_DIR
        filename = f"{model['name']}_export.json"
        filepath = os.path.join(out_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(export, f, ensure_ascii=False, indent=2)

        return {"ok": True, "path": filepath, "filename": filename}

    finally:
        conn.close()


@app.post("/api/models/import")
def import_model(req: ImportModelRequest):
    """Import a model configuration from an export JSON.

    Creates a new model with the given name and all column definitions,
    data tables, presets, and filter presets from the export data.
    """
    data = req.data
    if data.get('version') != 1:
        raise HTTPException(400, f"Unsupported export version: {data.get('version')}")

    model_data = data.get('model', {})
    data_types = data.get('data_types', {})
    if not model_data or not data_types:
        raise HTTPException(400, "Invalid export data: missing model or data_types")

    conn = get_db()
    try:
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "Model name must not be empty")

        # Check name uniqueness — auto-suffix if conflict
        base_name = name
        suffix = 1
        while conn.execute("SELECT id FROM aircraft_models WHERE name=?", (name,)).fetchone():
            name = f"{base_name} ({suffix})"
            suffix += 1

        # ── Create model ──
        conn.execute(
            """INSERT INTO aircraft_models
               (name, has_header, has_uav_send_id, extract_serial_from_path)
               VALUES (?, ?, ?, ?)""",
            (
                name,
                1 if model_data.get('has_header') else 0,
                1 if model_data.get('has_uav_send_id') else 0,
                1 if model_data.get('extract_serial_from_path') else 0,
            )
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # ── Insert data_table_registry + column_registry ──
        for dt_key, dt_def in data_types.items():
            table_name = data_table_name(model_id, dt_key)
            patterns_json = json.dumps(
                dt_def.get('file_patterns', []), ensure_ascii=False
            )
            is_alert = 1 if dt_def.get('is_alert') else 0

            conn.execute(
                """INSERT INTO data_table_registry
                   (model_id, data_type_key, table_name, display_label, file_patterns, is_alert)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (model_id, dt_key, table_name, dt_def['display_label'], patterns_json, is_alert)
            )

            for col in dt_def.get('columns', []):
                col_type = col.get('type', 'REAL')
                ordinal = col.get('ordinal')
                is_numeric = 1 if col_type.upper() in ('REAL', 'INTEGER', 'FLOAT') else 0
                scale_factor = col.get('scale_factor', 1.0)

                conn.execute(
                    """INSERT INTO column_registry
                       (model_id, data_type_key, table_name, column_name,
                        display_label, unit, data_type, ordinal, is_numeric, scale_factor)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        model_id, dt_key, table_name, col['name'],
                        col.get('label', col['name']), col.get('unit', ''),
                        col_type, ordinal, is_numeric, scale_factor,
                    )
                )

        # ── Import presets ──
        for preset in data.get('presets', []):
            cols_json = json.dumps(preset.get('columns', []), ensure_ascii=False)
            try:
                conn.execute(
                    "INSERT INTO presets (model_id, name, columns_json) VALUES (?, ?, ?)",
                    (model_id, preset['name'], cols_json)
                )
            except Exception:
                pass  # skip duplicates

        # ── Import filter presets ──
        for fp in data.get('filter_presets', []):
            cfg_json = json.dumps(fp.get('config', {}), ensure_ascii=False)
            try:
                conn.execute(
                    "INSERT INTO filter_presets (model_id, name, config_json) VALUES (?, ?, ?)",
                    (model_id, fp['name'], cfg_json)
                )
            except Exception:
                pass  # skip duplicates

        # ── Build config dict for register_model_tables ──
        config = {
            'has_header': bool(model_data.get('has_header')),
            'has_uav_send_id': bool(model_data.get('has_uav_send_id')),
            'extract_serial_from_path': bool(model_data.get('extract_serial_from_path')),
            'data_types': {},
        }
        for dt_key, dt_def in data_types.items():
            config['data_types'][dt_key] = {
                'display_label': dt_def['display_label'],
                'file_patterns': dt_def.get('file_patterns', []),
                'is_alert': dt_def.get('is_alert', False),
                'columns': [
                    {
                        'name': c['name'],
                        'label': c.get('label', c['name']),
                        'unit': c.get('unit', ''),
                        'type': c.get('type', 'REAL'),
                        'ordinal': c.get('ordinal'),
                        'scale_factor': c.get('scale_factor', 1.0),
                    }
                    for c in dt_def.get('columns', [])
                ],
            }

        register_model_tables(conn, model_id, config=config)
        conn.commit()

        return {"id": model_id, "name": name}

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# ─── Model Column Routes ────────────────────────────────────

@app.get("/api/models/{model_id}/columns")
def get_model_columns(model_id: int):
    """Get all columns for a model with full metadata, grouped by data type.

    Returns more detail than /api/registry/columns (includes column_name, ordinal).
    """
    from collections import OrderedDict
    conn = get_db()
    try:
        model = conn.execute("SELECT id FROM aircraft_models WHERE id=?", (model_id,)).fetchone()
        if not model:
            raise HTTPException(404, "Model not found")

        rows = conn.execute(
            """SELECT dtr.data_type_key, dtr.display_label, dtr.table_name,
                      cr.column_name, cr.display_label as col_label, cr.unit,
                      cr.data_type, cr.ordinal, cr.scale_factor
               FROM data_table_registry dtr
               JOIN column_registry cr ON cr.model_id = dtr.model_id
                   AND cr.data_type_key = dtr.data_type_key
               WHERE dtr.model_id = ?
               ORDER BY dtr.data_type_key, cr.ordinal""",
            (model_id,)
        ).fetchall()

        groups = OrderedDict()
        for row in rows:
            tk = row['data_type_key']
            if tk not in groups:
                groups[tk] = {
                    'data_type_key': tk,
                    'table': row['table_name'],
                    'label': row['display_label'],
                    'columns': [],
                }
            groups[tk]['columns'].append({
                'column_name': row['column_name'],
                'display_label': row['col_label'],
                'unit': row['unit'] or '',
                'data_type': row['data_type'],
                'ordinal': row['ordinal'],
                'scale_factor': row['scale_factor'] if row['scale_factor'] is not None else 1.0,
            })

        return {"data_types": list(groups.values())}
    finally:
        conn.close()


@app.patch("/api/models/{model_id}/columns")
def update_model_column(
    model_id: int,
    data_type_key: str = Query(...),
    column_name: str = Query(...),
    req: UpdateColumnRequest | None = None,
):
    """Update a column's display label and/or unit.

    Updates both the column_registry in SQLite and the config JSON on disk.
    """
    if req is None or (req.display_label is None and req.unit is None and req.scale_factor is None):
        raise HTTPException(400, "At least one of display_label, unit, or scale_factor must be provided")

    conn = get_db()
    try:
        result = update_column_metadata(
            conn, model_id, data_type_key, column_name,
            display_label=req.display_label,
            unit=req.unit,
            scale_factor=req.scale_factor,
        )
        return result
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.patch("/api/models/{model_id}/data-types/{data_type_key}")
def update_data_type_label(
    model_id: int, data_type_key: str, req: UpdateDataTypeLabelRequest
):
    """Update the display_label for a data type group in data_table_registry."""
    if not req.display_label.strip():
        raise HTTPException(400, "display_label must not be empty")

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM data_table_registry WHERE model_id=? AND data_type_key=?",
            (model_id, data_type_key)
        ).fetchone()
        if not row:
            raise HTTPException(
                404, f"Data type '{data_type_key}' not found for model {model_id}"
            )

        conn.execute(
            "UPDATE data_table_registry SET display_label=? WHERE model_id=? AND data_type_key=?",
            (req.display_label.strip(), model_id, data_type_key)
        )
        conn.commit()
        return {"ok": True, "data_type_key": data_type_key, "display_label": req.display_label.strip()}
    finally:
        conn.close()


# ─── Aircraft Routes ───────────────────────────────────────

@app.get("/api/models/{model_id}/aircraft")
def list_aircraft(model_id: int):
    """List all aircraft under a model."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT a.*,
                      (SELECT COUNT(*) FROM flights f
                       WHERE f.aircraft_id = a.id
                         AND f.deleted_at IS NULL AND f.server_deleted_at IS NULL) as flight_count
               FROM aircraft a
               WHERE a.model_id = ?
                 AND a.deleted_at IS NULL
                 AND a.server_deleted_at IS NULL
               ORDER BY a.name""",
            (model_id,),
        ).fetchall()
        return {"aircraft": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/models/{model_id}/aircraft")
def create_aircraft(model_id: int, req: CreateAircraftRequest):
    """Add an aircraft to a model."""
    conn = get_db()
    try:
        model = conn.execute("SELECT id FROM aircraft_models WHERE id=?", (model_id,)).fetchone()
        if not model:
            raise HTTPException(404, "Model not found")
        conn.execute(
            "INSERT INTO aircraft (model_id, name) VALUES (?, ?)",
            (model_id, req.name)
        )
        aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return {"id": aid, "model_id": model_id, "name": req.name}
    except HTTPException:
        raise
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(400, f"Aircraft '{req.name}' already exists in this model")
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.patch("/api/aircraft/{aircraft_id}")
def update_aircraft(aircraft_id: int, req: UpdateAircraftRequest):
    """Update an aircraft's name."""
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM aircraft WHERE id=?", (aircraft_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Aircraft not found")
        conn.execute(
            """UPDATE aircraft
               SET name=?,
                   sync_state=CASE WHEN sync_state IN ('synced', 'server_cache') THEN 'dirty' ELSE sync_state END
               WHERE id=?""",
            (req.name.strip(), aircraft_id),
        )
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(400, f"Aircraft name '{req.name}' already exists in this model")
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.delete("/api/aircraft/{aircraft_id}")
def delete_aircraft(aircraft_id: int, request: Request, req: DeleteEntityRequest | None = None):
    """Delete an aircraft and all its flights (cascade)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM aircraft WHERE id=?", (aircraft_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Aircraft not found")
        scope = _delete_scope(row, req.scope if req else "auto")
        if scope == "server":
            server_id = row["server_id"]
            if server_id is None:
                raise HTTPException(400, "Aircraft has not been synced to the server")
            result = _server_delete(conn, request, req, "aircraft", int(server_id))
            _mark_local_server_deleted(conn, "aircraft", aircraft_id, result)
            conn.commit()
            return {"ok": True, "scope": "server", "server": result}
        _require_local_delete_capability(conn, request, "aircraft")
        if scope == "local_unsynced" and row["server_id"] is not None:
            raise HTTPException(400, "Server-backed aircraft cannot be deleted as local_unsynced")
        conn.execute("DELETE FROM aircraft WHERE id=?", (aircraft_id,))
        conn.commit()
        return {"ok": True, "scope": scope}
    finally:
        conn.close()


# ─── Flight Routes ─────────────────────────────────────────

@app.get("/api/flights")
def list_flights(
    model_id: int | None = None,
    aircraft_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    batch_name: str | None = None,
    location: str | None = None,
    weather: str | None = None,
    payload: str | None = None,
):
    """List all imported flights with model/aircraft info."""
    conn = get_db()
    try:
        flights = []
        for item in flight_repository.list_flights(conn, {
            "model_id": model_id,
            "aircraft_id": aircraft_id,
            "date_from": date_from,
            "date_to": date_to,
            "batch_name": batch_name,
            "location": location,
            "weather": weather,
            "payload": payload,
        }):
            item["raw_warnings"] = _parse_raw_warnings(item.get("raw_import_warnings"))
            flights.append(item)
        return {"flights": flights}
    finally:
        conn.close()


@app.get("/api/flights/{flight_id}")
def get_flight(flight_id: int):
    """Get flight details with available columns."""
    conn = get_db()
    try:
        flight = flight_repository.get_flight_detail(conn, flight_id)
        if not flight:
            raise HTTPException(404, "Flight not found")
        result = dict(flight)
        result["raw_warnings"] = _parse_raw_warnings(result.get("raw_import_warnings"))
        result['columns'] = analysis.get_columns_for_flight_api(flight_id)
        return result
    finally:
        conn.close()


@app.get("/api/runtime/context")
def get_runtime_context_api(request: Request):
    conn = get_db()
    try:
        payload = runtime_context.runtime_context(conn, _server_token(None, request))
        conn.commit()
        return payload
    finally:
        conn.close()


@app.patch("/api/runtime/config")
def patch_runtime_config(req: RuntimeConfigUpdate, request: Request):
    conn = get_db()
    try:
        runtime_context.update_runtime_config(conn, _model_dump(req, exclude_unset=True))
        payload = runtime_context.runtime_context(conn, _server_token(None, request))
        conn.commit()
        return payload
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.post("/api/server-auth/login")
def server_login(req: ServerLoginRequest):
    conn = get_db()
    try:
        if not runtime_context.get_sync_enabled(conn):
            raise HTTPException(400, "SYNC_ENABLED is false")
        server_base_url = sync_client.normalize_base_url(runtime_context.get_server_base_url(conn))
        return sync_client.login(server_base_url, req.username.strip(), req.password)
    except sync_client.SyncClientError as e:
        raise HTTPException(e.status_code or 502, e.to_error_json("server_login"))
    finally:
        conn.close()


@app.post("/api/server-auth/logout")
def server_logout(request: Request):
    token = _server_token(None, request)
    conn = get_db()
    try:
        server_base_url = runtime_context.get_server_base_url(conn)
        if server_base_url and token:
            try:
                sync_client.logout(server_base_url, token=token)
            except sync_client.SyncClientError:
                pass
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/flights/{flight_id}/raw-files")
def get_flight_raw_files(flight_id: int):
    """List archived raw files attached to a flight."""
    conn = get_db()
    try:
        flight = flight_repository.get_flight_raw_warning_row(conn, flight_id)
        if not flight:
            raise HTTPException(404, "Flight not found")
        return {
            "flight_id": flight_id,
            "files": get_raw_files_for_flight(conn, flight_id),
            "warnings": _parse_raw_warnings(flight["raw_import_warnings"]),
        }
    finally:
        conn.close()


@app.get("/api/flights/{flight_id}/raw-manifest")
def get_flight_raw_manifest(flight_id: int):
    """Return and persist the current logical raw-file manifest for a flight."""
    conn = get_db()
    try:
        manifest = build_flight_manifest(conn, flight_id)
        if not manifest:
            raise HTTPException(404, "Flight not found")
        return manifest
    finally:
        conn.close()


# ─── Offline Sync Export Routes ────────────────────────────

@app.get("/api/sync/export-tree")
def get_sync_export_tree(q: str | None = None):
    """Return model -> aircraft -> batch -> flight selection tree."""
    keyword = (q or "").strip().lower()
    conn = get_db()
    try:
        models = {}
        for item in flight_repository.export_tree_rows(conn):
            item = dict(item)
            haystack = " ".join(
                str(item.get(k) or "")
                for k in (
                    "model_name", "aircraft_name", "record_batch_name",
                    "flight_name", "session_key", "flight_date",
                    "record_location", "record_weather",
                )
            ).lower()
            if keyword and keyword not in haystack:
                continue

            model = models.setdefault(
                item["model_id"],
                {"id": item["model_id"], "name": item["model_name"], "aircraft": {}},
            )
            aircraft = model["aircraft"].setdefault(
                item["aircraft_id"],
                {"id": item["aircraft_id"], "name": item["aircraft_name"], "batches": {}},
            )
            batch_name = item["record_batch_name"] or "未填写批次"
            batch = aircraft["batches"].setdefault(
                batch_name,
                {"name": batch_name, "flights": []},
            )
            batch["flights"].append(
                {
                    "id": item["flight_id"],
                    "name": item["flight_name"],
                    "session_key": item["session_key"],
                    "flight_date": item["flight_date"],
                    "start_time": item["start_time"],
                    "duration_sec": item["duration_sec"],
                    "record_location": item["record_location"],
                    "record_weather": item["record_weather"],
                }
            )

        tree = []
        for model in models.values():
            aircraft_list = []
            for aircraft in model["aircraft"].values():
                aircraft_list.append(
                    {
                        "id": aircraft["id"],
                        "name": aircraft["name"],
                        "batches": list(aircraft["batches"].values()),
                    }
                )
            tree.append({"id": model["id"], "name": model["name"], "aircraft": aircraft_list})
        flight_count = sum(
            len(batch["flights"])
            for model in tree
            for aircraft in model["aircraft"]
            for batch in aircraft["batches"]
        )
        return {"tree": tree, "flight_count": flight_count}
    finally:
        conn.close()


def _json_or_none(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return {"raw": value}


def _server_token(req, request: Request) -> str | None:
    body_token = getattr(req, "server_token", None)
    if body_token:
        return body_token
    token = request.headers.get("x-server-token")
    if token:
        return token.strip()
    auth = request.headers.get("authorization")
    if auth:
        return auth.strip()
    return None


def _server_header_token(request: Request) -> str | None:
    token = request.headers.get("x-server-token")
    return token.strip() if token else None


def _server_token_from_query(server_token: str | None, request: Request) -> str | None:
    if server_token:
        return server_token
    return _server_token(None, request)


SERVER_BACKED_DELETE_STATES = {"synced", "server_cache", "dirty"}


def _delete_scope(row, requested_scope: str | None) -> str:
    scope = (requested_scope or "auto").strip()
    if scope not in {"auto", "local_cache", "local_unsynced", "server"}:
        raise HTTPException(400, "Unsupported delete scope")
    if scope != "auto":
        return scope

    sync_state = row["sync_state"] if "sync_state" in row.keys() else None
    server_id = row["server_id"] if "server_id" in row.keys() else None
    if server_id is not None and sync_state in SERVER_BACKED_DELETE_STATES:
        return "server"
    if sync_state == "server_deleted" or server_id is not None:
        return "local_cache"
    return "local_unsynced"


def _require_local_delete_capability(conn, request: Request, entity_type: str) -> None:
    capability = {
        "model": "delete_models",
        "aircraft": "delete_aircraft",
        "flight": "delete_flights",
    }[entity_type]
    try:
        require_capability(conn, request, capability)
        return
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        local_denied = exc

    token = _server_header_token(request)
    if token and runtime_context.get_sync_enabled(conn):
        server_base_url = runtime_context.get_server_base_url(conn)
        if server_base_url:
            try:
                auth_payload = sync_client.auth_me(server_base_url, token=token, timeout=2)
                capabilities = auth_payload.get("capabilities")
                if isinstance(capabilities, list) and capability in capabilities:
                    return
            except sync_client.SyncClientError:
                pass
    raise local_denied


def _server_delete(
    conn,
    request: Request,
    req: DeleteEntityRequest | None,
    entity_type: str,
    server_id: int,
) -> dict:
    if not runtime_context.get_sync_enabled(conn):
        raise HTTPException(400, "SYNC_ENABLED is false")
    server_base_url = sync_client.normalize_base_url(runtime_context.get_server_base_url(conn))
    try:
        return sync_client.delete_entity(
            server_base_url,
            entity_type,
            server_id,
            reason=req.reason if req else None,
            token=_server_token(req, request),
        )
    except sync_client.SyncClientError as e:
        status = e.status_code or 502
        raise HTTPException(status, e.to_error_json(f"delete_{entity_type}"))


def _mark_local_server_deleted(conn, entity_type: str, local_id: int, result: dict) -> None:
    deleted_at = result.get("deleted_at") or datetime.now().isoformat(timespec="seconds")
    version = int(result.get("version") or 1)
    if entity_type == "model":
        conn.execute(
            """UPDATE aircraft_models
               SET sync_state='server_deleted', server_deleted_at=?, server_version=?,
                   last_sync_at=datetime('now','localtime')
               WHERE id=?""",
            (deleted_at, version, local_id),
        )
        conn.execute(
            """UPDATE aircraft
               SET sync_state='server_deleted', server_deleted_at=?,
                   last_sync_at=datetime('now','localtime')
               WHERE model_id=?""",
            (deleted_at, local_id),
        )
        conn.execute(
            """UPDATE flights
               SET sync_state='server_deleted', server_deleted_at=?,
                   last_sync_at=datetime('now','localtime')
               WHERE aircraft_id IN (SELECT id FROM aircraft WHERE model_id=?)""",
            (deleted_at, local_id),
        )
    elif entity_type == "aircraft":
        conn.execute(
            """UPDATE aircraft
               SET sync_state='server_deleted', server_deleted_at=?, server_version=?,
                   last_sync_at=datetime('now','localtime')
               WHERE id=?""",
            (deleted_at, version, local_id),
        )
        conn.execute(
            """UPDATE flights
               SET sync_state='server_deleted', server_deleted_at=?,
                   last_sync_at=datetime('now','localtime')
               WHERE aircraft_id=?""",
            (deleted_at, local_id),
        )
    else:
        conn.execute(
            """UPDATE flights
               SET sync_state='server_deleted', server_deleted_at=?, server_version=?,
                   last_sync_at=datetime('now','localtime')
               WHERE id=?""",
            (deleted_at, version, local_id),
        )


def _delete_local_model(conn, model_id: int) -> None:
    tables = conn.execute(
        "SELECT table_name FROM data_table_registry WHERE model_id=?", (model_id,)
    ).fetchall()
    conn.execute("DELETE FROM aircraft_models WHERE id=?", (model_id,))
    for t in tables:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {t['table_name']}")
        except Exception:
            pass


@app.get("/api/sync/queue")
def get_sync_queue():
    """Return local flights waiting for upload-oriented synchronization."""
    conn = get_db()
    try:
        items = []
        for row in sync_repository.list_upload_queue(conn):
            item = dict(row)
            item["sync_error"] = _json_or_none(item.get("sync_error_json"))
            items.append(item)
        return {
            "summary": sync_repository.upload_queue_summary(conn),
            "items": items,
        }
    finally:
        conn.close()


@app.post("/api/sync/export")
def post_sync_export(req: SyncExportRequest):
    """Export selected flights to the fixed sync_exports directory."""
    conn = get_db()
    try:
        result = export_package(conn, req.flight_ids)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.post("/api/sync/push-batch")
def post_sync_push_batch(req: SyncPushBatchRequest):
    """Generate the internal push_batch bundle for selected queued flights."""
    conn = get_db()
    try:
        if req.flight_ids is None:
            flight_ids = [
                int(item["id"])
                for item in sync_repository.list_upload_queue(conn, sync_repository.UPLOAD_QUEUE_STATES)
            ]
        else:
            flight_ids = req.flight_ids
        selected = sync_repository.validate_uploadable_flights(conn, flight_ids)
        result = export_package(conn, [int(item["id"]) for item in selected], bundle_kind="push_batch")
        return {
            **result,
            "status": "bundle_generated",
            "selected_flights": selected,
            "summary": sync_repository.upload_queue_summary(conn),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.post("/api/sync/push")
@app.post("/api/sync/retry")
def post_sync_push(req: SyncPushBatchRequest, request: Request):
    """Push selected queued flights to the configured collaboration server."""
    conn = get_db()
    run_id = None
    selected_ids: list[int] = []
    try:
        if req.flight_ids is None:
            flight_ids = [
                int(item["id"])
                for item in sync_repository.list_upload_queue(conn, sync_repository.UPLOAD_QUEUE_STATES)
            ]
        else:
            flight_ids = req.flight_ids
        selected = sync_repository.validate_uploadable_flights(conn, flight_ids)
        skipped_dirty = [item for item in selected if item.get("sync_state") == "dirty"]
        selected = [item for item in selected if item.get("sync_state") != "dirty"]
        selected_ids = [int(item["id"]) for item in selected]
        if not selected_ids:
            raise ValueError("dirty 元数据推送需要服务器更新协议支持，当前阶段不会自动标记为已同步")
        if not runtime_context.get_sync_enabled(conn):
            raise sync_client.SyncClientError("SYNC_ENABLED is false")
        server_base_url = sync_client.normalize_base_url(runtime_context.get_server_base_url(conn))
        run_id = sync_repository.create_sync_run(conn, "push")
        bundle = export_package(conn, selected_ids, bundle_kind="push_batch")
        conn.commit()

        manifest = sync_client.read_bundle_manifest(bundle["path"])
        token = _server_token(req, request)
        preflight = sync_client.preflight(server_base_url, manifest, token=token)
        if preflight.get("status") == "conflict" or preflight.get("conflicts"):
            sync_repository.mark_conflict(conn, selected_ids, preflight)
            summary = {
                "status": "conflict",
                "selected_flight_ids": selected_ids,
                "bundle": bundle,
                "preflight": preflight,
            }
            sync_repository.finish_sync_run(
                conn,
                run_id,
                "failed",
                summary=summary,
                error={"phase": "preflight", "report": preflight},
            )
            conn.commit()
            return {
                "ok": False,
                "status": "conflict",
                "run_id": run_id,
                "selected_flights": selected,
                "skipped_dirty": skipped_dirty,
                "bundle": bundle,
                "preflight": preflight,
                "summary": sync_repository.upload_queue_summary(conn),
            }

        server_report = sync_client.push_bundle(server_base_url, bundle["path"], token=token)
        if not server_report.get("ok"):
            error = {"phase": "push", "report": server_report}
            if server_report.get("status") == "conflict" or server_report.get("conflicts"):
                sync_repository.mark_conflict(conn, selected_ids, server_report)
            else:
                sync_repository.mark_upload_failed(conn, selected_ids, error)
            sync_repository.finish_sync_run(
                conn,
                run_id,
                "failed",
                summary={"selected_flight_ids": selected_ids, "bundle": bundle, "preflight": preflight},
                error=error,
            )
            conn.commit()
            return {
                "ok": False,
                "status": server_report.get("status") or "failed",
                "run_id": run_id,
                "selected_flights": selected,
                "skipped_dirty": skipped_dirty,
                "bundle": bundle,
                "preflight": preflight,
                "server_report": server_report,
                "summary": sync_repository.upload_queue_summary(conn),
            }

        writeback = sync_repository.apply_push_report(conn, server_report, selected_ids)
        status = "success" if not writeback["missing_flight_ids"] else "partial"
        run_status = "success" if status == "success" else "failed"
        response_summary = {
            "selected_flight_ids": selected_ids,
            "bundle": bundle,
            "preflight_summary": preflight.get("summary"),
            "server_imported": server_report.get("imported"),
            "server_existing": server_report.get("existing"),
            "writeback": writeback,
        }
        sync_repository.finish_sync_run(conn, run_id, run_status, summary=response_summary)
        conn.commit()
        return {
            "ok": status == "success",
            "status": status,
            "run_id": run_id,
            "selected_flights": selected,
            "skipped_dirty": skipped_dirty,
            "bundle": bundle,
            "preflight": preflight,
            "server_report": server_report,
            "writeback": writeback,
            "summary": sync_repository.upload_queue_summary(conn),
        }
    except sync_client.SyncClientError as e:
        error = e.to_error_json("push")
        if selected_ids:
            sync_repository.mark_upload_failed(conn, selected_ids, error)
        if run_id is not None:
            sync_repository.finish_sync_run(conn, run_id, "failed", error=error)
        conn.commit()
        raise HTTPException(502, error)
    except ValueError as e:
        if run_id is not None:
            sync_repository.finish_sync_run(conn, run_id, "failed", error={"phase": "local", "message": str(e)})
            conn.commit()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.post("/api/sync/run")
def post_sync_run(req: SyncRunRequest, request: Request):
    """Run one manual sync cycle: push queued local flights, then pull server data into local cache."""
    steps = []
    push_result = None
    pull_result = None

    conn = get_db()
    try:
        if req.flight_ids is None:
            queue_rows = sync_repository.list_upload_queue(conn, sync_repository.UPLOAD_QUEUE_STATES)
            push_ids = [
                int(item["id"])
                for item in queue_rows
                if item.get("sync_state") in {"pending_upload", "upload_failed"}
            ]
            dirty_count = sum(1 for item in queue_rows if item.get("sync_state") == "dirty")
        else:
            clean_ids = sorted({int(fid) for fid in req.flight_ids})
            if clean_ids:
                placeholders = ",".join("?" for _ in clean_ids)
                rows = conn.execute(
                    f"SELECT id, sync_state FROM flights WHERE id IN ({placeholders})",
                    clean_ids,
                ).fetchall()
                push_ids = [
                    int(row["id"])
                    for row in rows
                    if row["sync_state"] in {"pending_upload", "upload_failed"}
                ]
                dirty_count = sum(1 for row in rows if row["sync_state"] == "dirty")
            else:
                push_ids = []
                dirty_count = 0
    finally:
        conn.close()

    if push_ids:
        push_result = post_sync_push(
            SyncPushBatchRequest(flight_ids=push_ids, server_token=req.server_token),
            request,
        )
        steps.append({"name": "push", "status": push_result.get("status", "unknown")})
        if not push_result.get("ok"):
            return {
                "ok": False,
                "status": push_result.get("status") or "push_failed",
                "steps": steps,
                "push": push_result,
                "pull": None,
                "summary": push_result.get("summary"),
            }
    else:
        detail = "无可上传项"
        if dirty_count:
            detail = f"跳过 {dirty_count} 个 dirty 项，当前阶段需人工处理后上传"
        steps.append({"name": "push", "status": "skipped", "detail": detail})

    pull_result = post_sync_pull(SyncPullRequest(since=req.since, server_token=req.server_token), request)
    steps.append({"name": "pull", "status": pull_result.get("status", "unknown")})
    return {
        "ok": bool(pull_result.get("ok")),
        "status": "success" if pull_result.get("ok") else (pull_result.get("status") or "pull_failed"),
        "steps": steps,
        "push": push_result,
        "pull": pull_result,
        "summary": pull_result.get("summary"),
    }


@app.post("/api/sync/abandon")
def post_sync_abandon(req: SyncAbandonRequest):
    """Keep selected queued flights local-only so they no longer upload automatically."""
    conn = get_db()
    try:
        changed = sync_repository.abandon_uploads(conn, req.flight_ids)
        summary = sync_repository.upload_queue_summary(conn)
        conn.commit()
        return {"ok": True, "status": "abandoned", "abandoned": changed, "summary": summary}
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.get("/api/sync/changes")
def get_sync_changes(request: Request, since: str | None = Query(default=None), server_token: str | None = None):
    """Proxy server change summaries through the local backend."""
    conn = get_db()
    try:
        if not runtime_context.get_sync_enabled(conn):
            raise sync_client.SyncClientError("SYNC_ENABLED is false")
        server_base_url = sync_client.normalize_base_url(runtime_context.get_server_base_url(conn))
        cursor = since
        if cursor is None:
            cursor = sync_repository.get_setting(conn, "last_pull_cursor", "")
        return sync_client.changes(server_base_url, cursor, token=_server_token_from_query(server_token, request))
    except sync_client.SyncClientError as e:
        raise HTTPException(502, e.to_error_json("changes"))
    finally:
        conn.close()


@app.post("/api/sync/pull")
def post_sync_pull(req: SyncPullRequest, request: Request):
    """Download a server pull_bundle and import it into the local cache."""
    conn = get_db()
    run_id = None
    bundle_path = None
    try:
        if not runtime_context.get_sync_enabled(conn):
            raise sync_client.SyncClientError("SYNC_ENABLED is false")
        server_base_url = sync_client.normalize_base_url(runtime_context.get_server_base_url(conn))
        since = req.since
        if since is None:
            since = sync_repository.get_setting(conn, "last_pull_cursor", "")
        run_id = sync_repository.create_sync_run(conn, "pull")
        conn.commit()

        cache_dir = os.path.join(DATA_DIR, "sync_cache")
        os.makedirs(cache_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bundle_path = os.path.join(cache_dir, f"server_pull_{stamp}.fapkg")
        manifest = sync_client.download_bundle(
            server_base_url,
            since,
            bundle_path,
            token=_server_token(req, request),
        )
        report = import_pull_bundle(conn, bundle_path)
        ok = report.get("status") in {"success", "partial"}
        summary = {
            "since": since,
            "server_cursor": manifest.get("server_cursor"),
            "bundle_path": bundle_path,
            "manifest_counts": {
                "models": len(manifest.get("models") or []),
                "aircraft": len(manifest.get("aircraft") or []),
                "flights": len(manifest.get("flights") or []),
                "raw_files": len(manifest.get("raw_files") or []),
            },
            "report": report,
        }
        sync_repository.finish_sync_run(
            conn,
            run_id,
            "success" if ok else "failed",
            summary=summary,
            error=None if ok else {"phase": "import", "report": report},
        )
        conn.commit()
        return {
            "ok": ok,
            "status": report.get("status"),
            "run_id": run_id,
            "bundle": {
                "path": bundle_path,
                "package_id": manifest.get("package_id"),
                "server_cursor": manifest.get("server_cursor"),
            },
            "report": report,
            "summary": sync_repository.upload_queue_summary(conn),
        }
    except sync_client.SyncClientError as e:
        error = e.to_error_json("pull")
        if run_id is not None:
            sync_repository.finish_sync_run(conn, run_id, "failed", error=error)
            conn.commit()
        raise HTTPException(502, error)
    except ValueError as e:
        if run_id is not None:
            sync_repository.finish_sync_run(conn, run_id, "failed", error={"phase": "local", "message": str(e)})
            conn.commit()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.get("/api/sync/runs/{run_id}")
def get_sync_run(run_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM sync_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Sync run not found")
        item = dict(row)
        item["summary"] = _json_or_none(item.get("summary_json"))
        item["error"] = _json_or_none(item.get("error_json"))
        return item
    finally:
        conn.close()


@app.post("/api/sync/import/preview")
def post_sync_import_preview(req: SyncImportPreviewRequest):
    """Read a .fapkg package and return the import plan without writing data."""
    conn = get_db()
    try:
        return preview_import(conn, req.package_path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.post("/api/sync/import")
def post_sync_import(req: SyncImportRequest):
    """Import a confirmed field sync package into the current database."""
    if req.conflict_policy not in ("skip", "update_records"):
        raise HTTPException(400, "Unsupported conflict_policy")
    conn = get_db()
    try:
        options = {
            "model_actions": [
                _model_dump(item, exclude_unset=True) for item in req.model_actions
            ],
            "aircraft_mappings": [
                _model_dump(item, exclude_unset=True) for item in req.aircraft_mappings
            ],
            "conflict_policy": req.conflict_policy,
        }
        report = import_package(conn, req.package_path, options)
        return report
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.get("/api/sync/imports/{import_id}")
def get_sync_import(import_id: int):
    conn = get_db()
    try:
        report = get_import_report(conn, import_id)
        if not report:
            raise HTTPException(404, "Import report not found")
        return report
    finally:
        conn.close()


@app.delete("/api/flights/{flight_id}")
def delete_flight(flight_id: int, request: Request, req: DeleteEntityRequest | None = None):
    """Delete a flight and all its data."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Flight not found")
        scope = _delete_scope(row, req.scope if req else "auto")
        if scope == "server":
            server_id = row["server_id"]
            if server_id is None:
                raise HTTPException(400, "Flight has not been synced to the server")
            result = _server_delete(conn, request, req, "flight", int(server_id))
            _mark_local_server_deleted(conn, "flight", flight_id, result)
            conn.commit()
            return {"ok": True, "scope": "server", "server": result}
        _require_local_delete_capability(conn, request, "flight")
        if scope == "local_unsynced" and row["server_id"] is not None:
            raise HTTPException(400, "Server-backed flight cannot be deleted as local_unsynced")
        flight_repository.delete_flight(conn, flight_id)
        conn.commit()
        return {"ok": True, "scope": scope}
    finally:
        conn.close()


@app.patch("/api/flights/{flight_id}")
def update_flight(flight_id: int, req: UpdateFlightRequest):
    """Update flight metadata (e.g. rename)."""
    if not req.name.strip():
        raise HTTPException(400, "Name cannot be empty")
    conn = get_db()
    try:
        if not flight_repository.flight_exists(conn, flight_id):
            raise HTTPException(404, "Flight not found")
        flight_repository.update_flight_name(conn, flight_id, req.name.strip())
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.patch("/api/flights/{flight_id}/record")
def update_flight_record(flight_id: int, req: FlightRecordRequest):
    """Update manual flight record fields without touching parsed metrics."""
    data = _normalize_record_fields(_model_dump(req, exclude_unset=True), include_unset=False)
    if not data:
        return {"ok": True}

    conn = get_db()
    try:
        if not flight_repository.flight_exists(conn, flight_id):
            raise HTTPException(404, "Flight not found")
        flight_repository.update_flight_record(conn, flight_id, data)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/flights/scan")
def scan_folder_api(req: ImportRequest):
    """Scan a folder for flight sessions.

    Auto-detects the data format, matches against existing models or
    auto-creates a new one. Returns session-grouped preview with model info.
    """
    conn = get_db()
    try:
        result = scan_folder_sessions(req.source_path, conn=conn)
        return result
    finally:
        conn.close()


@app.post("/api/flights/import")
def import_flight_api(req: ImportSessionRequest):
    """Import a flight session (or all sessions if session_key is empty)."""
    try:
        record_fields = _normalize_record_fields(_model_dump(req), include_unset=True)
        flight_date = _normalize_flight_date(req.flight_date)
        if not flight_date:
            raise HTTPException(400, "飞行日期必填")
        if req.session_key:
            result = import_session(
                os.path.normpath(req.source_path), req.aircraft_id, req.session_key,
                record_fields=record_fields,
                flight_date_override=flight_date,
            )
            return result
        else:
            # Import all sessions for this aircraft
            conn = get_db()
            try:
                preview = scan_folder_sessions(req.source_path, conn=conn)
                imported = []
                for sess in preview.get('sessions', []):
                    result = import_session(
                        os.path.normpath(req.source_path), req.aircraft_id, sess['session_key'],
                        record_fields=record_fields,
                        flight_date_override=flight_date,
                    )
                    if 'error' not in result:
                        imported.append(result)
                if not imported:
                    return {'error': 'No sessions could be imported'}
                return {'imported': imported}
            finally:
                conn.close()
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


# ─── Data Routes ───────────────────────────────────────────

@app.get("/api/flights/{flight_id}/columns")
def get_columns(flight_id: int):
    """Get all available columns for a flight, grouped by data type."""
    return {"columns": analysis.get_columns_for_flight_api(flight_id)}


@app.post("/api/flights/{flight_id}/aligned")
def get_aligned(flight_id: int, req: AlignedRequest):
    """Get time-aligned multi-column data."""
    result = analysis.get_aligned_data(
        flight_id, req.column_keys, req.ref_table, req.tolerance,
        filter_spec=req.filter
    )
    return result


@app.get("/api/flights/{flight_id}/alerts")
def get_alerts(flight_id: int):
    """Get alerts for a flight."""
    conn = get_db()
    try:
        model_id = analysis._get_model_id(conn, flight_id)
        if model_id is None:
            return {"alerts": []}

        alert_dt_key, alert_table = analysis._get_alert_data_type(conn, model_id)
        if not alert_table:
            return {"alerts": []}

        # Read alert columns from column_registry (ordered by ordinal)
        alert_col_rows = conn.execute(
            "SELECT column_name FROM column_registry "
            "WHERE model_id=? AND data_type_key=? AND ordinal IS NOT NULL "
            "ORDER BY ordinal",
            (model_id, alert_dt_key)
        ).fetchall()

        if not alert_col_rows:
            return {"alerts": []}

        col_names = [r['column_name'] for r in alert_col_rows]
        cols_str = ', '.join(col_names)

        try:
            rows = conn.execute(
                f"SELECT time_str, time_sec, {cols_str} "
                f"FROM {alert_table} WHERE flight_id=? ORDER BY time_sec",
                (flight_id,)
            ).fetchall()
        except Exception:
            return {"alerts": []}

        # Map to frontend-compatible {desc, extra} format.
        desc_col = next((c for c in col_names if 'desc' in c.lower()), col_names[0] if col_names else None)
        extra_candidates = [c for c in col_names if 'extra' in c.lower()]
        extra_col = extra_candidates[0] if extra_candidates else (
            col_names[-1] if len(col_names) > 1 else None
        )

        return {"alerts": [
            {"time_str": r["time_str"], "time_sec": r["time_sec"],
             "desc": str(r[desc_col]) if desc_col and r[desc_col] is not None else '',
             "extra": str(r[extra_col]) if extra_col and r[extra_col] is not None else ''}
            for r in rows
        ]}
    finally:
        conn.close()


@app.get("/api/flights/{flight_id}/stats")
def get_stats(flight_id: int):
    """Get flight statistics summary."""
    return analysis.get_flight_stats(flight_id)


# ─── Analysis Routes ───────────────────────────────────────

@app.post("/api/flights/{flight_id}/correlation")
def correlation(flight_id: int, req: CorrelationRequest):
    """Compute correlation matrix for selected columns."""
    return analysis.get_correlation(flight_id, req.column_keys)


@app.post("/api/flights/{flight_id}/anomaly")
def anomaly(flight_id: int, req: AnomalyRequest):
    """Detect anomalies in a single column."""
    return analysis.get_anomalies(
        flight_id, req.column_key, req.window_size, req.sigma
    )


@app.post("/api/compare")
def compare(req: CompareRequest):
    """Compare one metric across multiple flights."""
    return {"series": analysis.get_compare(req.flight_ids, req.column_key)}


# ─── Column Registry Routes ────────────────────────────────

@app.get("/api/registry/columns")
def registry_columns(model_id: int):
    """Get all columns registered for a model, grouped by data type."""
    conn = get_db()
    try:
        result = get_columns_for_model(conn, model_id)
        return {"columns": result}
    finally:
        conn.close()


# ─── Preset Routes ─────────────────────────────────────────

@app.get("/api/presets")
def list_presets(model_id: int):
    """List column presets for a given model."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM presets WHERE model_id=? ORDER BY name",
            (model_id,)
        ).fetchall()
        return {"presets": [
            {"id": r['id'], "model_id": r['model_id'], "name": r['name'], "columns": json.loads(r['columns_json'])}
            for r in rows
        ]}
    finally:
        conn.close()


@app.post("/api/presets")
def create_preset(req: PresetCreate):
    """Save a column selection preset."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO presets (model_id, name, columns_json) VALUES (?, ?, ?)",
            (req.model_id, req.name, json.dumps(req.columns))
        )
        conn.commit()
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"id": pid, "model_id": req.model_id, "name": req.name, "columns": req.columns}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: int):
    """Delete a preset."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ─── Filter Preset Routes ──────────────────────────────────

@app.get("/api/filter-presets")
def list_filter_presets(model_id: int):
    """List filter presets for a given model."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM filter_presets WHERE model_id=? ORDER BY name",
            (model_id,)
        ).fetchall()
        return {"presets": [
            {"id": r['id'], "model_id": r['model_id'], "name": r['name'], "config": json.loads(r['config_json'])}
            for r in rows
        ]}
    finally:
        conn.close()


@app.post("/api/filter-presets")
def create_filter_preset(req: FilterPresetCreate):
    """Save a filter configuration preset."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO filter_presets (model_id, name, config_json) VALUES (?, ?, ?)",
            (req.model_id, req.name, json.dumps(req.config))
        )
        conn.commit()
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return {"id": pid, "model_id": req.model_id, "name": req.name, "config": req.config}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.delete("/api/filter-presets/{preset_id}")
def delete_filter_preset(preset_id: int):
    """Delete a filter preset."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM filter_presets WHERE id=?", (preset_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ─── Serve Frontend ────────────────────────────────────────

if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


# ─── Entry Point ───────────────────────────────────────────

def _show_error(title, msg):
    """Show error to user — MessageBox in GUI mode, stderr otherwise."""
    full_msg = f"{msg}\n\n日志文件: {STARTUP_LOG_PATH}"
    _startup_log(f"{title}: {msg}")
    if sys.platform == 'win32':
        try:
            ctypes.windll.user32.MessageBoxW(0, str(full_msg), str(title), 0x10)
            return
        except Exception:
            pass
    try:
        if sys.stderr:
            print(f"[{title}] {full_msg}", file=sys.stderr)
    except Exception:
        pass


_server_error = None


def _build_log_config():
    """Build a uvicorn log config for PyInstaller frozen (no-console) mode.

    Why this exists: under ``console=False`` (FlightAnalyzer.spec), PyInstaller
    leaves ``sys.stdout`` as ``None``. uvicorn's default formatter calls
    ``sys.stdout.isatty()`` and crashes with ``AttributeError: 'NoneType'
    object has no attribute 'isatty'``. This config forces ``use_colors=False``
    and routes both handlers to ``ext://sys.stderr`` (which is still attached),
    sidestepping the ``None`` stdout. Dev mode (non-frozen) keeps uvicorn's
    default config via ``run_server`` so colored output is preserved.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
                "use_colors": False,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                "use_colors": False,
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        },
    }


def _find_available_port(start=18520, max_attempts=10):
    """Return the first available localhost port in a small range."""
    import socket
    for offset in range(max_attempts):
        port = start + offset
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            _startup_log(f"Port {port} is available")
            return port
        except OSError as e:
            _startup_log(f"Port {port} is unavailable: {e}")
        finally:
            sock.close()
    return None


def _sleep_forever():
    try:
        while True:
            _time.sleep(1)
    except KeyboardInterrupt:
        pass


def run_server(port=18520):
    """Start uvicorn in a daemon thread."""
    global _server_error
    import uvicorn
    _startup_log(f"Starting uvicorn on http://127.0.0.1:{port}")
    try:
        if getattr(sys, 'frozen', False):
            uvicorn.run(
                app, host="127.0.0.1", port=port,
                log_config=_build_log_config(),
            )
        else:
            uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    except Exception as e:
        _server_error = f"{e}\n{traceback.format_exc()}"
        _startup_log(f"Server failed to start: {_server_error}")
        _show_error("Server Error", f"Server failed to start:\n{_server_error}")


def _wait_for_server(server_thread, port, timeout=10):
    """Wait until the server thread is actually listening on the port."""
    import socket
    start = _time.time()
    while _time.time() - start < timeout:
        if not server_thread.is_alive() or _server_error is not None:
            return False
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.3)
            s.close()
            return True
        except (OSError, ConnectionRefusedError):
            _time.sleep(0.2)
    return False


def main():
    _startup_log("=== Starting Flight Analyzer ===")
    _startup_log(f"BASE_DIR={BASE_DIR}")
    _startup_log(f"FRONTEND_DIR={FRONTEND_DIR} exists={os.path.isdir(FRONTEND_DIR)}")
    _startup_log(f"DATA_DIR={DATA_DIR}")
    _startup_log(f"DB_PATH={DB_PATH}")

    try:
        db_result = init_db()
        _startup_log(f"Database initialized: {db_result}")
    except Exception as e:
        details = f"{e}\n{traceback.format_exc()}"
        _startup_log(f"Database initialization failed: {details}")
        _show_error(
            "Database Error",
            "数据库初始化失败，应用无法启动。\n\n"
            f"数据目录: {DATA_DIR}\n"
            f"数据库: {DB_PATH}\n\n"
            f"错误: {e}",
        )
        sys.exit(1)

    port = _find_available_port(18520, 10)
    if port is None:
        _show_error(
            "Startup Error",
            "无法找到可用的本地端口（18520-18529）。\n"
            "请关闭其他实例或占用这些端口的程序后重试。",
        )
        sys.exit(1)

    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    if not _wait_for_server(server_thread, port, timeout=15):
        msg = f"Server did not start on port {port} within timeout.\n\n"
        if _server_error:
            msg += f"Server error:\n{_server_error}"
        else:
            msg += "Check startup.log for backend import or startup errors."
        _startup_log(msg)
        _show_error("Startup Error", msg)
        sys.exit(1)

    app_url = f"http://127.0.0.1:{port}"
    _startup_log(f"Server ready at {app_url}")

    if os.path.isdir(FRONTEND_DIR):
        try:
            import webview
            _startup_log("Opening pywebview window")
            webview.create_window(
                "Flight Analyzer",
                app_url,
                width=1400, height=900,
                min_size=(1024, 680),
            )
            # Enable DevTools (right-click → Inspect, or press F12).
            # TODO: set to False before shipping a release build.
            webview.start(debug=True)
        except ImportError as e:
            _startup_log(f"pywebview unavailable, falling back to browser: {e}")
            import webbrowser
            webbrowser.open(app_url)
            _sleep_forever()
        except Exception as e:
            _startup_log(f"pywebview failed: {e}\n{traceback.format_exc()}")
            _show_error("WebView Error", f"桌面窗口启动失败：\n{e}")
            sys.exit(1)
    else:
        _startup_log("Frontend dist not found, falling back to browser")
        import webbrowser
        webbrowser.open(app_url)
        _sleep_forever()


if __name__ == "__main__":
    main()
