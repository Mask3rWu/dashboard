"""Flight Analyzer — FastAPI backend + pywebview desktop shell."""

import os
import sys
import json
import threading
import time as _time
import traceback
import ctypes

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
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

from backend.database import init_db, get_db
from backend.parser import import_session
from backend.format_configs import (
    register_model_tables, get_columns_for_model,
    get_table_name,
    save_model_config_to_db, generate_config_from_scan,
    update_column_metadata, data_table_name,
)
from backend.scanner import scan_folder_sessions
from backend import analysis

from datetime import datetime

# ─── App Setup ─────────────────────────────────────────────

app = FastAPI(title="Flight Analyzer", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic Models ───────────────────────────────────────

class ImportRequest(BaseModel):
    source_path: str
    format_category: str | None = None


class ImportSessionRequest(BaseModel):
    source_path: str
    aircraft_id: int       # required — aircraft.id
    session_key: str = ''  # empty = import all sessions for this aircraft


class UpdateFlightRequest(BaseModel):
    name: str


class CreateModelRequest(BaseModel):
    name: str
    format_category: str


class CreateModelFromScanRequest(BaseModel):
    name: str
    source_path: str
    format_category: str


class UpdateModelRequest(BaseModel):
    name: str


class UpdateColumnRequest(BaseModel):
    display_label: str | None = None
    unit: str | None = None
    scale_factor: float | None = None


class CreateAircraftRequest(BaseModel):
    serial_number: str
    name: str = ''


class UpdateAircraftRequest(BaseModel):
    serial_number: str


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


class PresetCreate(BaseModel):
    model_id: int
    name: str
    columns: list[str]


class FilterPresetCreate(BaseModel):
    model_id: int
    name: str
    config: dict


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
               (SELECT COUNT(*) FROM aircraft a WHERE a.model_id = am.id) as aircraft_count,
               COALESCE((SELECT COUNT(*) FROM flights f
                         JOIN aircraft a2 ON a2.id = f.aircraft_id
                         WHERE a2.model_id = am.id), 0) as total_flights,
               COALESCE((SELECT SUM(f2.duration_sec) FROM flights f2
                         JOIN aircraft a3 ON a3.id = f2.aircraft_id
                         WHERE a3.model_id = am.id), 0) as total_flight_hours
               FROM aircraft_models am ORDER BY am.created_at"""
        ).fetchall()
        return {"models": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/models")
def create_model(req: CreateModelRequest):
    """Create a new aircraft model. Automatically creates data tables and column registry."""
    if not req.format_category.strip():
        raise HTTPException(400, "format_category must not be empty")

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO aircraft_models (name, format_category) VALUES (?, ?)",
            (req.name, req.format_category)
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        register_model_tables(conn, model_id, req.format_category)
        return {"id": model_id, "name": req.name, "format_category": req.format_category}
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(400, f"Model name '{req.name}' already exists")
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.post("/api/models/from-scan")
def create_model_from_scan(req: CreateModelFromScanRequest):
    """Create a model from a scanned folder. Auto-generates format config
    from the folder's file structure."""
    conn = get_db()
    try:
        config_data = generate_config_from_scan(req.source_path)
        config_data['format'] = req.format_category

        conn.execute(
            "INSERT INTO aircraft_models (name, format_category) VALUES (?, ?)",
            (req.name, req.format_category)
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        save_model_config_to_db(conn, model_id, config_data)
        register_model_tables(conn, model_id, req.format_category, config=config_data)
        conn.commit()
        return {"id": model_id, "name": req.name, "format_category": req.format_category}
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
        conn.execute("UPDATE aircraft_models SET name=? WHERE id=?", (req.name.strip(), model_id))
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@app.delete("/api/models/{model_id}")
def delete_model(model_id: int):
    """Delete a model and all related aircraft, flights, and data (cascade).
    Also deletes the per-model format config file from disk."""
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM aircraft_models WHERE id=?", (model_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Model not found")

        # Get table names to drop (before cascade deletes registry rows)
        tables = conn.execute(
            "SELECT table_name FROM data_table_registry WHERE model_id=?", (model_id,)
        ).fetchall()

        # Cascade delete: aircraft_models → aircraft → flights → data rows
        # Also cascades to column_registry and data_table_registry
        conn.execute("DELETE FROM aircraft_models WHERE id=?", (model_id,))

        # Drop the now-empty per-model data tables
        for t in tables:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {t['table_name']}")
            except Exception:
                pass

        conn.commit()
        return {"ok": True}
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
            "SELECT name, format_category, has_header, has_uav_send_id, extract_serial_from_path "
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
                'format_category': model['format_category'],
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
    if not model_data.get('format_category') or not data_types:
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

        fmt_cat = model_data['format_category']

        # ── Create model ──
        conn.execute(
            """INSERT INTO aircraft_models
               (name, format_category, has_header, has_uav_send_id, extract_serial_from_path)
               VALUES (?, ?, ?, ?, ?)""",
            (
                name,
                fmt_cat,
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
            'format': fmt_cat,
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

        register_model_tables(conn, model_id, fmt_cat, config=config)
        conn.commit()

        return {"id": model_id, "name": name, "format_category": fmt_cat}

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


# ─── Aircraft Routes ───────────────────────────────────────

@app.get("/api/models/{model_id}/aircraft")
def list_aircraft(model_id: int):
    """List all aircraft under a model."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM flights f WHERE f.aircraft_id = a.id) as flight_count "
            "FROM aircraft a WHERE a.model_id = ? ORDER BY a.serial_number",
            (model_id,)
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
            "INSERT INTO aircraft (model_id, serial_number) VALUES (?, ?)",
            (model_id, req.serial_number)
        )
        aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return {"id": aid, "model_id": model_id, "serial_number": req.serial_number}
    except HTTPException:
        raise
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(400, f"Aircraft '{req.serial_number}' already exists in this model")
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.patch("/api/aircraft/{aircraft_id}")
def update_aircraft(aircraft_id: int, req: UpdateAircraftRequest):
    """Update an aircraft's serial number."""
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM aircraft WHERE id=?", (aircraft_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Aircraft not found")
        conn.execute("UPDATE aircraft SET serial_number=? WHERE id=?", (req.serial_number.strip(), aircraft_id))
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(400, f"Serial number '{req.serial_number}' already exists in this model")
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@app.delete("/api/aircraft/{aircraft_id}")
def delete_aircraft(aircraft_id: int):
    """Delete an aircraft and all its flights (cascade)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM aircraft WHERE id=?", (aircraft_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Aircraft not found")
        conn.execute("DELETE FROM aircraft WHERE id=?", (aircraft_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ─── Flight Routes ─────────────────────────────────────────

@app.get("/api/flights")
def list_flights():
    """List all imported flights with model/aircraft info."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT f.*, a.serial_number as aircraft_serial, a.name as aircraft_name,
                      am.id as model_id, am.name as model_name, am.format_category
               FROM flights f
               JOIN aircraft a ON a.id = f.aircraft_id
               JOIN aircraft_models am ON am.id = a.model_id
               ORDER BY f.import_time DESC"""
        ).fetchall()
        return {"flights": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/flights/{flight_id}")
def get_flight(flight_id: int):
    """Get flight details with available columns."""
    conn = get_db()
    try:
        flight = conn.execute(
            """SELECT f.*, a.serial_number as aircraft_serial, a.name as aircraft_name,
                      am.id as model_id, am.name as model_name, am.format_category
               FROM flights f
               JOIN aircraft a ON a.id = f.aircraft_id
               JOIN aircraft_models am ON am.id = a.model_id
               WHERE f.id=?""",
            (flight_id,)
        ).fetchone()
        if not flight:
            raise HTTPException(404, "Flight not found")
        result = dict(flight)
        result['columns'] = analysis.get_columns_for_flight_api(flight_id)
        return result
    finally:
        conn.close()


@app.delete("/api/flights/{flight_id}")
def delete_flight(flight_id: int):
    """Delete a flight and all its data."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM flights WHERE id=?", (flight_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.patch("/api/flights/{flight_id}")
def update_flight(flight_id: int, req: UpdateFlightRequest):
    """Update flight metadata (e.g. rename)."""
    if not req.name.strip():
        raise HTTPException(400, "Name cannot be empty")
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM flights WHERE id=?", (flight_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Flight not found")
        conn.execute("UPDATE flights SET name=? WHERE id=?", (req.name.strip(), flight_id))
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
        if req.session_key:
            result = import_session(
                os.path.normpath(req.source_path), req.aircraft_id, req.session_key
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
                        os.path.normpath(req.source_path), req.aircraft_id, sess['session_key']
                    )
                    if 'error' not in result:
                        imported.append(result)
                if not imported:
                    return {'error': 'No sessions could be imported'}
                return {'imported': imported}
            finally:
                conn.close()
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

        alert_table = get_table_name(conn, model_id, 'alert')
        if not alert_table:
            return {"alerts": []}

        # Read alert columns from column_registry (ordered by ordinal)
        alert_col_rows = conn.execute(
            "SELECT column_name FROM column_registry "
            "WHERE model_id=? AND data_type_key='alert' AND ordinal IS NOT NULL "
            "ORDER BY ordinal",
            (model_id,)
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

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend", "dist")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


# ─── Entry Point ───────────────────────────────────────────

def _show_error(title, msg):
    """Show error to user — MessageBox in GUI mode, print otherwise."""
    if sys.platform == 'win32':
        try:
            ctypes.windll.user32.MessageBoxW(0, str(msg), str(title), 0x10)
            return
        except Exception:
            pass
    print(f"[{title}] {msg}", file=sys.stderr)


_server_error = None


def _build_log_config():
    """Build a log config that works in PyInstaller frozen (no-console) mode."""
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


def run_server(port=18520):
    """Start uvicorn in a daemon thread."""
    global _server_error
    import uvicorn
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
    print("Starting Flight Analyzer...")
    init_db()
    port = 18520

    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    if not _wait_for_server(server_thread, port, timeout=15):
        msg = f"Server did not start on port {port} within timeout.\n\n"
        if _server_error:
            msg += f"Server error:\n{_server_error}"
        else:
            msg += "Check that the backend modules can be imported correctly."
        _show_error("Startup Error", msg)
        sys.exit(1)

    print(f"Server ready at http://127.0.0.1:{port}")

    if os.path.isdir(FRONTEND_DIR):
        try:
            import webview
            webview.create_window(
                "Flight Analyzer",
                f"http://127.0.0.1:{port}",
                width=1400, height=900,
                min_size=(1024, 680),
            )
            # Enable DevTools (right-click → Inspect, or press F12).
            # TODO: set to False before shipping a release build.
            webview.start(debug=True)
        except ImportError:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{port}")
            print(f"Server running at http://127.0.0.1:{port}")
            print("Press Ctrl+C to exit.")
            try:
                while True:
                    _time.sleep(1)
            except KeyboardInterrupt:
                pass
    else:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}")
        print(f"Server running at http://127.0.0.1:{port}")
        print("Run: cd frontend && npm run build   to build the frontend")
        print("Then: cd frontend && npm run dev    for dev mode")
        print("Press Ctrl+C to exit.")
        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
