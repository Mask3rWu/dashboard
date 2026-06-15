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
from backend.parser import import_session, scan_folder_sessions
from backend.format_configs import (
    load_format_config, register_model_tables, get_columns_for_model,
    get_columns_for_flight, get_table_name,
)
from backend.scanner import detect_format
from backend import analysis

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
    mode: str = 'overwrite'


class UpdateFlightRequest(BaseModel):
    name: str


class CreateModelRequest(BaseModel):
    name: str
    format_category: str  # 'A', 'B', or 'C'
    description: str = ''


class UpdateModelRequest(BaseModel):
    name: str


class CreateAircraftRequest(BaseModel):
    serial_number: str
    name: str = ''


class UpdateAircraftRequest(BaseModel):
    serial_number: str


class AlignedRequest(BaseModel):
    column_keys: list[str]
    ref_table: str = "gps"
    tolerance: float = 0.5
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
    name: str
    columns: list[str]


class FilterPresetCreate(BaseModel):
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


# ─── Model Routes ──────────────────────────────────────────

@app.get("/api/models")
def list_models():
    """List all aircraft models."""
    conn = get_db()
    rows = conn.execute(
        "SELECT am.*, (SELECT COUNT(*) FROM aircraft a WHERE a.model_id = am.id) as aircraft_count "
        "FROM aircraft_models am ORDER BY am.created_at"
    ).fetchall()
    conn.close()
    return {"models": [dict(r) for r in rows]}


@app.post("/api/models")
def create_model(req: CreateModelRequest):
    """Create a new aircraft model. Automatically creates data tables and column registry."""
    if req.format_category not in ('A', 'B', 'C'):
        raise HTTPException(400, "format_category must be A, B, or C")

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO aircraft_models (name, format_category, description) VALUES (?, ?, ?)",
            (req.name, req.format_category, req.description)
        )
        model_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Create data tables and populate registry
        register_model_tables(conn, model_id, req.format_category)

        conn.close()
        return {"id": model_id, "name": req.name, "format_category": req.format_category}
    except Exception as e:
        conn.close()
        if 'UNIQUE' in str(e):
            raise HTTPException(400, f"Model name '{req.name}' already exists")
        raise HTTPException(500, str(e))


@app.patch("/api/models/{model_id}")
def update_model(model_id: int, req: UpdateModelRequest):
    """Rename an aircraft model."""
    conn = get_db()
    row = conn.execute("SELECT id FROM aircraft_models WHERE id=?", (model_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Model not found")
    try:
        conn.execute("UPDATE aircraft_models SET name=? WHERE id=?", (req.name.strip(), model_id))
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception as e:
        conn.close()
        raise HTTPException(400, str(e))


@app.delete("/api/models/{model_id}")
def delete_model(model_id: int):
    """Delete a model and all related aircraft, flights, and data (cascade)."""
    conn = get_db()
    row = conn.execute("SELECT id FROM aircraft_models WHERE id=?", (model_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Model not found")

    # Get table names to drop
    tables = conn.execute(
        "SELECT table_name FROM data_table_registry WHERE model_id=?", (model_id,)
    ).fetchall()

    conn.execute("DELETE FROM aircraft_models WHERE id=?", (model_id,))

    # Drop per-model data tables
    for t in tables:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {t['table_name']}")
        except Exception:
            pass

    conn.commit()
    conn.close()
    return {"ok": True}


# ─── Aircraft Routes ───────────────────────────────────────

@app.get("/api/models/{model_id}/aircraft")
def list_aircraft(model_id: int):
    """List all aircraft under a model."""
    conn = get_db()
    rows = conn.execute(
        "SELECT a.*, (SELECT COUNT(*) FROM flights f WHERE f.aircraft_id = a.id) as flight_count "
        "FROM aircraft a WHERE a.model_id = ? ORDER BY a.serial_number",
        (model_id,)
    ).fetchall()
    conn.close()
    return {"aircraft": [dict(r) for r in rows]}


@app.post("/api/models/{model_id}/aircraft")
def create_aircraft(model_id: int, req: CreateAircraftRequest):
    """Add an aircraft to a model."""
    conn = get_db()
    # Verify model exists
    model = conn.execute("SELECT id FROM aircraft_models WHERE id=?", (model_id,)).fetchone()
    if not model:
        conn.close()
        raise HTTPException(404, "Model not found")
    try:
        conn.execute(
            "INSERT INTO aircraft (model_id, serial_number) VALUES (?, ?)",
            (model_id, req.serial_number)
        )
        aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return {"id": aid, "model_id": model_id, "serial_number": req.serial_number}
    except Exception as e:
        conn.close()
        if 'UNIQUE' in str(e):
            raise HTTPException(400, f"Aircraft '{req.serial_number}' already exists in this model")
        raise HTTPException(500, str(e))


@app.patch("/api/aircraft/{aircraft_id}")
def update_aircraft(aircraft_id: int, req: UpdateAircraftRequest):
    """Update an aircraft's serial number."""
    conn = get_db()
    row = conn.execute("SELECT id FROM aircraft WHERE id=?", (aircraft_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Aircraft not found")
    try:
        conn.execute("UPDATE aircraft SET serial_number=? WHERE id=?", (req.serial_number.strip(), aircraft_id))
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception as e:
        conn.close()
        if 'UNIQUE' in str(e):
            raise HTTPException(400, f"Serial number '{req.serial_number}' already exists in this model")
        raise HTTPException(500, str(e))


@app.delete("/api/aircraft/{aircraft_id}")
def delete_aircraft(aircraft_id: int):
    """Delete an aircraft and all its flights (cascade)."""
    conn = get_db()
    row = conn.execute("SELECT id FROM aircraft WHERE id=?", (aircraft_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Aircraft not found")
    conn.execute("DELETE FROM aircraft WHERE id=?", (aircraft_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ─── Flight Routes ─────────────────────────────────────────

@app.get("/api/flights")
def list_flights():
    """List all imported flights with model/aircraft info."""
    conn = get_db()
    rows = conn.execute(
        """SELECT f.*, a.serial_number as aircraft_serial, a.name as aircraft_name,
                  am.id as model_id, am.name as model_name, am.format_category
           FROM flights f
           JOIN aircraft a ON a.id = f.aircraft_id
           JOIN aircraft_models am ON am.id = a.model_id
           ORDER BY f.import_time DESC"""
    ).fetchall()
    conn.close()
    return {"flights": [dict(r) for r in rows]}


@app.get("/api/flights/{flight_id}")
def get_flight(flight_id: int):
    """Get flight details with available columns."""
    conn = get_db()
    flight = conn.execute(
        """SELECT f.*, a.serial_number as aircraft_serial, a.name as aircraft_name,
                  am.id as model_id, am.name as model_name, am.format_category
           FROM flights f
           JOIN aircraft a ON a.id = f.aircraft_id
           JOIN aircraft_models am ON am.id = a.model_id
           WHERE f.id=?""",
        (flight_id,)
    ).fetchone()
    conn.close()
    if not flight:
        raise HTTPException(404, "Flight not found")
    result = dict(flight)
    result['columns'] = analysis.get_columns_for_flight_api(flight_id)
    return result


@app.delete("/api/flights/{flight_id}")
def delete_flight(flight_id: int):
    """Delete a flight and all its data."""
    conn = get_db()
    conn.execute("DELETE FROM flights WHERE id=?", (flight_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/flights/{flight_id}")
def update_flight(flight_id: int, req: UpdateFlightRequest):
    """Update flight metadata (e.g. rename)."""
    if not req.name.strip():
        raise HTTPException(400, "Name cannot be empty")
    conn = get_db()
    row = conn.execute("SELECT id FROM flights WHERE id=?", (flight_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Flight not found")
    conn.execute("UPDATE flights SET name=? WHERE id=?", (req.name.strip(), flight_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/flights/scan")
def scan_folder_api(req: ImportRequest):
    """Scan a folder for flight sessions. Returns session-grouped preview with import status."""
    conn = get_db()

    # Auto-detect format if not provided
    fmt = req.format_category
    if not fmt:
        fmt = detect_format(req.source_path)

    result = scan_folder_sessions(req.source_path, conn=conn)

    # Add format info
    if fmt:
        result['format_category'] = fmt
        result['format_detected'] = True

    conn.close()
    return result


@app.post("/api/flights/import")
def import_flight_api(req: ImportSessionRequest):
    """Import a flight session (or all sessions if session_key is empty)."""
    try:
        if req.session_key:
            result = import_session(
                req.source_path, req.aircraft_id, req.session_key, req.mode
            )
            return result
        else:
            # Import all sessions for this aircraft
            conn = get_db()
            preview = scan_folder_sessions(req.source_path, conn=conn)
            conn.close()

            imported = []
            for sess in preview.get('sessions', []):
                result = import_session(
                    req.source_path, req.aircraft_id, sess['session_key'], req.mode
                )
                if 'error' not in result:
                    imported.append(result)

            if not imported:
                return {'error': 'No sessions could be imported'}
            return {'imported': imported}
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
    model_id = analysis._get_model_id(conn, flight_id)
    if model_id is None:
        conn.close()
        return {"alerts": []}

    alert_table = get_table_name(conn, model_id, 'alert')
    if not alert_table:
        conn.close()
        return {"alerts": []}

    try:
        rows = conn.execute(
            f"SELECT time_str, time_sec, alert_desc, extra_value "
            f"FROM {alert_table} WHERE flight_id=? ORDER BY time_sec",
            (flight_id,)
        ).fetchall()
    except Exception:
        conn.close()
        return {"alerts": []}

    conn.close()
    return {"alerts": [
        {"time_str": r["time_str"], "time_sec": r["time_sec"],
         "desc": r["alert_desc"], "extra": r["extra_value"]}
        for r in rows
    ]}


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
    result = get_columns_for_model(conn, model_id)
    conn.close()
    return {"columns": result}


# ─── Preset Routes ─────────────────────────────────────────

@app.get("/api/presets")
def list_presets():
    """List all saved column presets."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM presets ORDER BY name").fetchall()
    conn.close()
    return {"presets": [{"id": r['id'], "name": r['name'], "columns": json.loads(r['columns_json'])} for r in rows]}


@app.post("/api/presets")
def create_preset(req: PresetCreate):
    """Save a column selection preset."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO presets (name, columns_json) VALUES (?, ?)",
            (req.name, json.dumps(req.columns))
        )
        conn.commit()
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return {"id": pid, "name": req.name, "columns": req.columns}
    except Exception as e:
        conn.close()
        raise HTTPException(400, str(e))


@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: int):
    """Delete a preset."""
    conn = get_db()
    conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ─── Filter Preset Routes ──────────────────────────────────

@app.get("/api/filter-presets")
def list_filter_presets():
    """List all saved filter presets."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM filter_presets ORDER BY name").fetchall()
    conn.close()
    return {"presets": [{"id": r['id'], "name": r['name'], "config": json.loads(r['config_json'])} for r in rows]}


@app.post("/api/filter-presets")
def create_filter_preset(req: FilterPresetCreate):
    """Save a filter configuration preset."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO filter_presets (name, config_json) VALUES (?, ?)",
            (req.name, json.dumps(req.config))
        )
        conn.commit()
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return {"id": pid, "name": req.name, "config": req.config}
    except Exception as e:
        conn.close()
        raise HTTPException(400, str(e))


@app.delete("/api/filter-presets/{preset_id}")
def delete_filter_preset(preset_id: int):
    """Delete a filter preset."""
    conn = get_db()
    conn.execute("DELETE FROM filter_presets WHERE id=?", (preset_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


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
            webview.start()
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
