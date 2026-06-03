"""Flight Analyzer — FastAPI backend + pywebview desktop shell."""

import os
import sys
import json
import threading
import time as _time
import traceback

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add parent to path for PyInstaller compatibility
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from backend.database import init_db, get_db
from backend.parser import import_flight, scan_folder
from backend import analysis

# ─── App Setup ─────────────────────────────────────────────

app = FastAPI(title="Flight Analyzer", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic Models ───────────────────────────────────────

class ImportRequest(BaseModel):
    source_path: str


class AlignedRequest(BaseModel):
    column_keys: list[str]
    ref_table: str = "gps_data"
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


# ─── Flight Routes ─────────────────────────────────────────

@app.get("/api/flights")
def list_flights():
    """List all imported flights."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM flights ORDER BY import_time DESC"
    ).fetchall()
    conn.close()
    return {"flights": [dict(r) for r in rows]}


@app.get("/api/flights/{flight_id}")
def get_flight(flight_id: int):
    """Get flight details with available columns."""
    conn = get_db()
    flight = conn.execute("SELECT * FROM flights WHERE id=?", (flight_id,)).fetchone()
    conn.close()
    if not flight:
        raise HTTPException(404, "Flight not found")
    result = dict(flight)
    result['columns'] = analysis.get_columns_for_flight(flight_id)
    return result


@app.delete("/api/flights/{flight_id}")
def delete_flight(flight_id: int):
    """Delete a flight and all its data."""
    conn = get_db()
    conn.execute("DELETE FROM flights WHERE id=?", (flight_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/flights/scan")
def scan_folder_api(req: ImportRequest):
    """Scan a folder without importing. Preview what will be imported."""
    files = scan_folder(req.source_path)
    if not files:
        return {"error": "No drone data files found", "files": []}
    # Group by drone_id
    from collections import defaultdict
    by_drone = defaultdict(lambda: defaultdict(int))
    for f in files:
        by_drone[f['drone_id']][f['data_type_key']] += 1
    preview = []
    for drone_id, types in by_drone.items():
        preview.append({
            'drone_id': drone_id,
            'file_count': sum(types.values()),
            'data_types': dict(types),
        })
    return {"files": preview}


@app.post("/api/flights/import")
def import_flight_api(req: ImportRequest):
    """Import a flight data folder."""
    try:
        result = import_flight(req.source_path)
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


# ─── Data Routes ───────────────────────────────────────────

@app.get("/api/flights/{flight_id}/columns")
def get_columns(flight_id: int):
    """Get all available columns for a flight, grouped by data type."""
    return {"columns": analysis.get_columns_for_flight(flight_id)}


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
    rows = conn.execute(
        "SELECT time_str, time_sec, alert_desc, extra_value "
        "FROM flight_alerts WHERE flight_id=? ORDER BY time_sec",
        (flight_id,)
    ).fetchall()
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

def run_server(port=18520):
    """Start uvicorn in a daemon thread."""
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main():
    init_db()
    port = 18520

    # Start server thread
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()
    _time.sleep(0.5)

    # If frontend is built, open pywebview; else open browser
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
